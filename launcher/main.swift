// Native stub for MeetingTranscriber.app. It starts the Python menu bar app as a child process,
// so macOS attributes Microphone / Screen Recording permissions to this app
// (a stable identity) instead of to Terminal or a bare Python binary.

import Foundation

var sigSources: [DispatchSourceSignal] = []
let res = Bundle.main.resourceURL!
let project = try! String(contentsOf: res.appendingPathComponent("project_path.txt"), encoding: .utf8)
    .trimmingCharacters(in: .whitespacesAndNewlines)

let p = Process()
p.executableURL = URL(fileURLWithPath: project + "/.venv/bin/python")
p.arguments = ["-m", "mt.menubar"]
p.currentDirectoryURL = URL(fileURLWithPath: project)
var env = ProcessInfo.processInfo.environment
env["MT_HELPER"] = res.appendingPathComponent("sck-audio").path
env["PATH"] = (env["PATH"] ?? "") + ":/opt/homebrew/bin:/usr/local/bin"
p.environment = env

// Keep a log: a crash at startup otherwise just makes the menu bar icon vanish.
let logDir = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Logs")
let logURL = logDir.appendingPathComponent("MeetingTranscriber.log")
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
