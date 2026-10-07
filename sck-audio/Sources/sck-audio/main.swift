// Captures all system audio via ScreenCaptureKit (the same mechanism OBS uses)
// and writes it to a 16 kHz mono WAV until SIGINT/SIGTERM or stdin closes.
//
// Usage: sck-audio <output.wav>
// stdout protocol: "FIRST <unix-epoch>" when the first audio buffer arrives,
//                  "ERROR <message>" on failure.

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

    init(url: URL) { self.url = url }

    func start() async throws {
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
        stream = s
    }

    func stop() async {
        try? await stream?.stopCapture()
        queue.sync { file = nil }  // closing the AVAudioFile finalises the WAV header
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sb: CMSampleBuffer, of type: SCStreamOutputType) {
        guard type == .audio, sb.isValid, sb.numSamples > 0 else { return }
        try? sb.withAudioBufferList { abl, _ in
            guard let desc = sb.formatDescription?.audioStreamBasicDescription else { return }
            var asbd = desc
            guard let fmt = AVAudioFormat(streamDescription: &asbd),
                let pcm = AVAudioPCMBuffer(pcmFormat: fmt, bufferListNoCopy: abl.unsafePointer)
            else { return }
            pcm.frameLength = AVAudioFrameCount(sb.numSamples)
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
                try file?.write(from: pcm)
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
        print("ERROR stream stopped: \(error.localizedDescription)")
        exit(2)
    }
}

guard CommandLine.arguments.count == 2 else {
    FileHandle.standardError.write(Data("usage: sck-audio <output.wav>\n".utf8))
    exit(64)
}

let capturer = Capturer(url: URL(fileURLWithPath: CommandLine.arguments[1]))
var stopping = false

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

// Stop if the parent process goes away (stdin closes).
DispatchQueue.global().async {
    while FileHandle.standardInput.availableData.count > 0 {}
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
