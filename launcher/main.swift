// Native stub for MeetingTranscriber.app. It starts the Python app as a child process,
// so macOS attributes Microphone / Screen Recording permissions to this app
// (a stable identity) instead of to Terminal or a bare Python binary.

import Foundation

var sigSources: [DispatchSourceSignal] = []
let res = Bundle.main.resourceURL!
let project = try! String(contentsOf: res.appendingPathComponent("project_path.txt"), encoding: .utf8)
    .trimmingCharacters(in: .whitespacesAndNewlines)

let p = Process()
p.executableURL = URL(fileURLWithPath: project + "/.venv/bin/python")
p.arguments = ["-m", "mt.app"]
p.currentDirectoryURL = URL(fileURLWithPath: project)
var env = ProcessInfo.processInfo.environment
env["MT_HELPER"] = res.appendingPathComponent("sck-audio").path
env["PATH"] = (env["PATH"] ?? "") + ":/opt/homebrew/bin:/usr/local/bin"
p.environment = env

// Keep a log: a crash at startup otherwise just makes the floating bar vanish.
let logDir = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Logs")
let logURL = logDir.appendingPathComponent("MeetingTranscriber.log")
// Keep one previous log (MeetingTranscriber.log.1) once this one passes 5 MB, so it never grows without bound.
if let size = (try? FileManager.default.attributesOfItem(atPath: logURL.path))?[.size] as? Int, size > 5_000_000 {
    let old = logDir.appendingPathComponent("MeetingTranscriber.log.1")
    try? FileManager.default.removeItem(at: old)
    try? FileManager.default.moveItem(at: logURL, to: old)
}
if !FileManager.default.fileExists(atPath: logURL.path) {
    FileManager.default.createFile(atPath: logURL.path, contents: nil)
}
if let log = try? FileHandle(forWritingTo: logURL) {
    log.seekToEndOfFile()
    p.standardOutput = log
    p.standardError = log
}

for sig in [SIGTERM, SIGINT] {
    signal(sig, SIG_IGN)
    let s = DispatchSource.makeSignalSource(signal: sig, queue: .main)
    s.setEventHandler { p.terminate() }
    s.resume()
    sigSources.append(s)
}

p.terminationHandler = { proc in exit(proc.terminationStatus) }
do { try p.run() } catch {
    FileHandle.standardError.write(Data("cannot start python: \(error)\n".utf8))
    exit(1)
}
RunLoop.main.run()
