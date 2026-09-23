// dl2read — capture the Drug Lord 2 window and OCR it.
// ScreenCaptureKit (captures the window on any Space, even occluded) + Vision.
// Emits JSON: window size + recognized text boxes with pixel coordinates.
//
// Build:  swiftc -O -o bin/dl2read dl2read.swift
// Run:    bin/dl2read [ownerSubstring] [--save out.png]   (default owner "druglord")
//
// Notes: SCK needs the main run loop pumping, so the work runs async and we exit
// from inside the final completion. NSApplication(.accessory) gives the CLI a
// WindowServer connection.

import ScreenCaptureKit
import Vision
import CoreGraphics
import Foundation
import ImageIO
import UniformTypeIdentifiers
import AppKit

func die(_ msg: String, _ code: Int32 = 1) -> Never {
    FileHandle.standardError.write((msg + "\n").data(using: .utf8)!)
    exit(code)
}

// ---- args ----
var want = "druglord"
var savePath: String? = nil
var showCursor = false
var i = 1
let args = CommandLine.arguments
while i < args.count {
    if args[i] == "--save", i + 1 < args.count { savePath = args[i+1]; i += 2 }
    else if args[i] == "--cursor" { showCursor = true; i += 1 }
    else { want = args[i].lowercased(); i += 1 }
}

func savePNG(_ img: CGImage, _ path: String) {
    guard let dst = CGImageDestinationCreateWithURL(URL(fileURLWithPath: path) as CFURL,
                                                    UTType.png.identifier as CFString, 1, nil) else { return }
    CGImageDestinationAddImage(dst, img, nil)
    CGImageDestinationFinalize(dst)
}

func ocr(_ img: CGImage) -> [[String: Any]] {
    let W = CGFloat(img.width), H = CGFloat(img.height)
    var boxes: [[String: Any]] = []
    let req = VNRecognizeTextRequest()
    req.recognitionLevel = .accurate
    req.usesLanguageCorrection = false
    req.recognitionLanguages = ["en-US"]
    let handler = VNImageRequestHandler(cgImage: img, options: [:])
    do { try handler.perform([req]) } catch { die("OCR failed: \(error)") }
    for obs in (req.results ?? []) {
        guard let cand = obs.topCandidates(1).first else { continue }
        let bb = obs.boundingBox                      // normalized, bottom-left origin
        boxes.append([
            "text": cand.string,
            "x": Int((bb.minX * W).rounded()),
            "y": Int(((1 - bb.maxY) * H).rounded()),  // convert to top-left origin
            "w": Int((bb.width * W).rounded()),
            "h": Int((bb.height * H).rounded()),
            "conf": cand.confidence
        ])
    }
    return boxes
}

var winFrame: [String: Int] = [:]

func emit(_ img: CGImage) -> Never {
    if let sp = savePath { savePNG(img, sp) }
    let payload: [String: Any] = ["width": img.width, "height": img.height,
                                  "frame": winFrame, "boxes": ocr(img)]
    let data = try! JSONSerialization.data(withJSONObject: payload, options: [.sortedKeys])
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write("\n".data(using: .utf8)!)
    exit(0)
}

func start() {
    SCShareableContent.getExcludingDesktopWindows(true, onScreenWindowsOnly: false) { content, err in
        guard let content = content else { die("shareable content error: \(String(describing: err))") }
        // Only the game's own windows (owner druglord2.exe) — never the Terminal,
        // which may contain "drug lord" in its title/scrollback.
        let game = content.windows.filter {
            ($0.owningApplication?.applicationName ?? "").lowercased().contains("druglord")
        }
        let win = game.first { ($0.title ?? "").lowercased().contains(want) }
            ?? game.first { !($0.title ?? "").lowercased().contains("world") }  // default: main
            ?? game.first
        guard let win = win else {
            var msg = "no window matching '\(want)'. On-screen windows:\n"
            for w in content.windows.prefix(60) {
                let o = w.owningApplication?.applicationName ?? "?"
                msg += "  owner=[\(o)] title=[\(w.title ?? "")] \(Int(w.frame.width))x\(Int(w.frame.height))\n"
            }
            die(msg)
        }
        winFrame = ["x": Int(win.frame.origin.x), "y": Int(win.frame.origin.y),
                    "w": Int(win.frame.width), "h": Int(win.frame.height)]
        let filter = SCContentFilter(desktopIndependentWindow: win)
        let cfg = SCStreamConfiguration()
        cfg.width = Int(win.frame.width * 2)
        cfg.height = Int(win.frame.height * 2)
        cfg.showsCursor = showCursor
        SCScreenshotManager.captureImage(contentFilter: filter, configuration: cfg) { cg, cerr in
            guard let cg = cg else { die("capture error: \(String(describing: cerr))") }
            emit(cg)
        }
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)
DispatchQueue.main.async { start() }
app.run()
