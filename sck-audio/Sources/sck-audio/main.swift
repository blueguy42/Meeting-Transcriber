// Captures all system audio via ScreenCaptureKit (the same mechanism OBS uses)
// and writes it to a 16 kHz mono WAV until SIGINT/SIGTERM or stdin closes.
//
// Usage: sck-audio <output.wav> [--muted]   (--muted: start with silence, no gap before a MUTE arrives)
// stdin commands:  "MUTE" / "UNMUTE" - while muted the file keeps growing but holds silence,
//                  so the recording stays aligned with the clock (and what was said is not kept).
// stdout protocol: "FIRST <unix-epoch>" when the first audio buffer arrives,
//                  "ERROR <message>" on failure.
// If the stream is stopped under us (e.g. the Mac went to sleep) it is restarted and the gap is
// filled with silence, so the file stays aligned with the clock.

import AVFoundation
import CoreMedia
import Foundation
import ScreenCaptureKit

setvbuf(stdout, nil, _IOLBF, 0)

final class Capturer: NSObject, SCStreamOutput, SCStreamDelegate {
    private let url: URL
    private var stream: SCStream?
    private var file: AVAudioFile?
    private let queue = DispatchQueue(label: "sck-audio.samples")
    private var announced = false
    private var muted = false  // only touched on `queue`, so it stays in order with the samples
    private var framesWritten: Int64 = 0  // `queue` only
    private var firstBufferAt: Date?  // `queue` only
    private var resync = false  // `queue` only: the stream was restarted, pad the gap with silence
    private var lastBufferAt = Date()  // `queue` only
    private var restarting = false  // main-actor-ish: guarded by `restartLock`
    private let restartLock = NSLock()

    init(url: URL, muted: Bool) {
        self.url = url
        self.muted = muted
    }

    func start() async throws {
        stream = try await makeStream()
        queue.async { self.lastBufferAt = Date() }
        startWatchdog()
    }

    private func makeStream() async throws -> SCStream {
        let content = try await SCShareableContent.excludingDesktopWindows(
            false, onScreenWindowsOnly: false)
        guard let display = content.displays.first else {
            throw NSError(
                domain: "sck-audio", code: 1,
                userInfo: [NSLocalizedDescriptionKey: "no display found"])
        }
        let filter = SCContentFilter(display: display, excludingWindows: [])
        let cfg = SCStreamConfiguration()
        cfg.capturesAudio = true
        cfg.excludesCurrentProcessAudio = true
        cfg.sampleRate = 16000
        cfg.channelCount = 1
        // We only want audio; keep the unavoidable video stream tiny and slow.
        cfg.width = 2
        cfg.height = 2
        cfg.minimumFrameInterval = CMTime(value: 1, timescale: 1)
        cfg.queueDepth = 3

        let s = SCStream(filter: filter, configuration: cfg, delegate: self)
        try s.addStreamOutput(self, type: .audio, sampleHandlerQueue: queue)
        try await s.startCapture()
        return s
    }

    /// Replaces a dead stream (sleep/wake, display change, ...). Retries until it works.
    func restart() async {
        restartLock.lock()
        if restarting { restartLock.unlock(); return }
        restarting = true
        restartLock.unlock()
        defer { restartLock.lock(); restarting = false; restartLock.unlock() }

        let old = stream
        stream = nil
        try? await old?.stopCapture()
        var delay: UInt64 = 1
        while !stopping {
            do {
                queue.sync { resync = true }
                stream = try await makeStream()
                queue.async { self.lastBufferAt = Date() }
                print("RESTARTED")
                return
            } catch {
                print("ERROR restart failed: \(error.localizedDescription)")
                try? await Task.sleep(nanoseconds: delay * 1_000_000_000)
                delay = min(delay * 2, 10)
            }
        }
    }

    /// A stream can also go quiet without reporting an error; screen capture delivers buffers
    /// continuously (silent ones included), so a long gap means it is dead.
    private func startWatchdog() {
        Task {
            while !stopping {
                try? await Task.sleep(nanoseconds: 5_000_000_000)
                let idle = queue.sync { Date().timeIntervalSince(lastBufferAt) }
                if idle > 15 && stream != nil { await restart() }
            }
        }
    }

    func setMuted(_ value: Bool) {
        queue.async { self.muted = value }
    }

    func stop() async {
        try? await stream?.stopCapture()
        queue.sync { file = nil }  // closing the AVAudioFile finalises the WAV header
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sb: CMSampleBuffer, of type: SCStreamOutputType) {
        guard type == .audio, sb.isValid, sb.numSamples > 0 else { return }
        lastBufferAt = Date()
        try? sb.withAudioBufferList { abl, _ in
            guard let desc = sb.formatDescription?.audioStreamBasicDescription else { return }
            var asbd = desc
            guard let fmt = AVAudioFormat(streamDescription: &asbd),
                let pcm = AVAudioPCMBuffer(pcmFormat: fmt, bufferListNoCopy: abl.unsafePointer)
            else { return }
            pcm.frameLength = AVAudioFrameCount(sb.numSamples)
            if muted {
                for buf in abl { if let data = buf.mData { memset(data, 0, Int(buf.mDataByteSize)) } }
            }
            do {
                if file == nil {
                    file = try AVAudioFile(
                        forWriting: url,
                        settings: [
                            AVFormatIDKey: kAudioFormatLinearPCM,
                            AVSampleRateKey: fmt.sampleRate,
                            AVNumberOfChannelsKey: fmt.channelCount,
                            AVLinearPCMBitDepthKey: 16,
                            AVLinearPCMIsFloatKey: false,
                            AVLinearPCMIsBigEndianKey: false,
                        ],
                        commonFormat: fmt.commonFormat,
                        interleaved: fmt.isInterleaved)
                }
                if firstBufferAt == nil { firstBufferAt = Date() }
                if resync {
                    resync = false
                    let expected = Int64(Date().timeIntervalSince(firstBufferAt!) * fmt.sampleRate)
                    let gap = expected - framesWritten - Int64(sb.numSamples)
                    if gap > 0, let silence = AVAudioPCMBuffer(
                        pcmFormat: fmt, frameCapacity: AVAudioFrameCount(gap))
                    {
                        silence.frameLength = AVAudioFrameCount(gap)
                        let list = UnsafeMutableAudioBufferListPointer(silence.mutableAudioBufferList)
                        for b in list { if let d = b.mData { memset(d, 0, Int(b.mDataByteSize)) } }
                        try file?.write(from: silence)
                        framesWritten += gap
                    }
                }
                try file?.write(from: pcm)
                framesWritten += Int64(pcm.frameLength)
                if !announced {
                    announced = true
                    print("FIRST \(Date().timeIntervalSince1970)")
                }
            } catch {
                print("ERROR write failed: \(error.localizedDescription)")
            }
        }
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        print("ERROR stream stopped: \(error.localizedDescription) - restarting")
        Task { await self.restart() }
    }
}

let args = Array(CommandLine.arguments.dropFirst())
guard args.count == 1 || (args.count == 2 && args[1] == "--muted") else {
    FileHandle.standardError.write(Data("usage: sck-audio <output.wav> [--muted]\n".utf8))
    exit(64)
}

let capturer = Capturer(url: URL(fileURLWithPath: args[0]), muted: args.count == 2)
var stopping = false  // read from several tasks; only ever flips to true

func shutdown() {
    if stopping { return }
    stopping = true
    Task {
        await capturer.stop()
        exit(0)
    }
}

var sources: [DispatchSourceSignal] = []
for sig in [SIGINT, SIGTERM] {
    signal(sig, SIG_IGN)
    let src = DispatchSource.makeSignalSource(signal: sig, queue: .main)
    src.setEventHandler { shutdown() }
    src.resume()
    sources.append(src)
}

// Commands arrive on stdin; stop if the parent process goes away (stdin closes).
DispatchQueue.global().async {
    while true {
        let data = FileHandle.standardInput.availableData
        if data.isEmpty { break }
        for line in String(decoding: data, as: UTF8.self).split(whereSeparator: \.isNewline) {
            switch line.trimmingCharacters(in: .whitespaces) {
            case "MUTE": capturer.setMuted(true)
            case "UNMUTE": capturer.setMuted(false)
            default: break
            }
        }
    }
    shutdown()
}

Task {
    do {
        try await capturer.start()
    } catch {
        print("ERROR \(error.localizedDescription)")
        exit(1)
    }
}

RunLoop.main.run()
