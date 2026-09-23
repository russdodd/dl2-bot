// dl2windows — print JSON of the game's on-screen windows (id/title/bounds), so the
// tool can locate the Info popup menu (a separate small window) and click its items
// by real screen position.
import CoreGraphics
import Foundation

let opts = CGWindowListOption(arrayLiteral: .optionOnScreenOnly, .excludeDesktopElements)
let arr = (CGWindowListCopyWindowInfo(opts, kCGNullWindowID) as? [[String: Any]]) ?? []
var out: [[String: Any]] = []
for w in arr {
    let owner = (w[kCGWindowOwnerName as String] as? String) ?? ""
    guard owner.lowercased().contains("druglord") else { continue }
    var b: [String: Int] = [:]
    if let d = w[kCGWindowBounds as String] as? [String: Any] {
        b = ["x": Int(d["X"] as? Double ?? 0), "y": Int(d["Y"] as? Double ?? 0),
             "w": Int(d["Width"] as? Double ?? 0), "h": Int(d["Height"] as? Double ?? 0)]
    }
    out.append(["id": (w[kCGWindowNumber as String] as? Int) ?? -1,
                "title": (w[kCGWindowName as String] as? String) ?? "", "bounds": b])
}
let data = try JSONSerialization.data(withJSONObject: out)
FileHandle.standardOutput.write(data)
FileHandle.standardOutput.write("\n".data(using: .utf8)!)
