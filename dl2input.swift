// dl2input — post synthetic mouse/keyboard events (for the drug-list sweep).
// Requires Terminal to have Accessibility permission, or events won't reach the game.
//
// Build: swiftc -O -o bin/dl2input dl2input.swift
// Use:   dl2input click <x> <y>      # left click at global screen point (points)
//        dl2input key <keycode>      # tap a key (Home=115, Down=125, End=119)
//        dl2input raise <ownerSub>   # bring matching app to the front

import CoreGraphics
import AppKit
import Foundation

let a = CommandLine.arguments
guard a.count >= 2 else { FileHandle.standardError.write("need a command\n".data(using:.utf8)!); exit(2) }

switch a[1] {
case "click" where a.count >= 4:
    let x = Double(a[2]) ?? 0, y = Double(a[3]) ?? 0
    let p = CGPoint(x: x, y: y)
    let src = CGEventSource(stateID: .hidSystemState)
    CGWarpMouseCursorPosition(p)
    let down = CGEvent(mouseEventSource: src, mouseType: .leftMouseDown, mouseCursorPosition: p, mouseButton: .left)
    let up   = CGEvent(mouseEventSource: src, mouseType: .leftMouseUp,   mouseCursorPosition: p, mouseButton: .left)
    down?.post(tap: .cghidEventTap)
    usleep(30_000)
    up?.post(tap: .cghidEventTap)

case "move" where a.count >= 4:
    CGWarpMouseCursorPosition(CGPoint(x: Double(a[2]) ?? 0, y: Double(a[3]) ?? 0))

case "dclick" where a.count >= 4:
    let p = CGPoint(x: Double(a[2]) ?? 0, y: Double(a[3]) ?? 0)
    let src = CGEventSource(stateID: .hidSystemState)
    CGWarpMouseCursorPosition(p)
    for n in 1...2 {
        let d = CGEvent(mouseEventSource: src, mouseType: .leftMouseDown, mouseCursorPosition: p, mouseButton: .left)
        let u = CGEvent(mouseEventSource: src, mouseType: .leftMouseUp,   mouseCursorPosition: p, mouseButton: .left)
        d?.setIntegerValueField(.mouseEventClickState, value: Int64(n))
        u?.setIntegerValueField(.mouseEventClickState, value: Int64(n))
        d?.post(tap: .cghidEventTap); usleep(20_000); u?.post(tap: .cghidEventTap); usleep(40_000)
    }

case "key" where a.count >= 3:
    let code = CGKeyCode(UInt16(a[2]) ?? 0)
    let src = CGEventSource(stateID: .hidSystemState)
    CGEvent(keyboardEventSource: src, virtualKey: code, keyDown: true)?.post(tap: .cghidEventTap)
    usleep(20_000)
    CGEvent(keyboardEventSource: src, virtualKey: code, keyDown: false)?.post(tap: .cghidEventTap)

case "num" where a.count >= 3:
    // Type digits via US keycodes (Wine reads these where Unicode events don't stick).
    let map: [Character: CGKeyCode] = ["0": 29, "1": 18, "2": 19, "3": 20, "4": 21,
                                       "5": 23, "6": 22, "7": 26, "8": 28, "9": 25]
    let src = CGEventSource(stateID: .hidSystemState)
    for ch in a[2] {
        guard let code = map[ch] else { continue }
        CGEvent(keyboardEventSource: src, virtualKey: code, keyDown: true)?.post(tap: .cghidEventTap)
        usleep(15_000)
        CGEvent(keyboardEventSource: src, virtualKey: code, keyDown: false)?.post(tap: .cghidEventTap)
        usleep(15_000)
    }

case "raise" where a.count >= 3:
    let want = a[2].lowercased()
    let apps = NSWorkspace.shared.runningApplications
    if let app = apps.first(where: { ($0.localizedName ?? "").lowercased().contains(want)
                                     || ($0.bundleIdentifier ?? "").lowercased().contains(want) }) {
        app.activate(options: [.activateAllWindows])
        print("raised \(app.localizedName ?? "?")")
    } else {
        FileHandle.standardError.write("no app matching \(want)\n".data(using:.utf8)!); exit(1)
    }

default:
    FileHandle.standardError.write("usage: click x y | key code | raise owner\n".data(using:.utf8)!); exit(2)
}
