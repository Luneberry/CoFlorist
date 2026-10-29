import Foundation
import ImageIO
import CoreGraphics
import UniformTypeIdentifiers
let root = URL(fileURLWithPath: CommandLine.arguments[1])
let source = root.appendingPathComponent(CommandLine.arguments.count > 2 ? CommandLine.arguments[2] : "assets/cat-expressions-portrait-1280x1920.png")
let input = CGImageSourceCreateWithURL(source as CFURL, nil)!
let atlas = CGImageSourceCreateImageAtIndex(input, 0, nil)!
precondition(atlas.width == 1280 && atlas.height == 1920)
let frameDirectory = CommandLine.arguments.count > 3 ? CommandLine.arguments[3] : "assets/frames"
try FileManager.default.createDirectory(at:root.appendingPathComponent(frameDirectory),withIntermediateDirectories:true)
let names = ["neutral", "happy", "sad", "angry", "surprised", "thinking", "listening", "speaking"]
for (index,name) in names.enumerated() {
    for frame in 0..<2 {
        let x = (index % 2 * 2 + frame) * 320
        let y = (index / 2) * 480
        let tile = atlas.cropping(to: CGRect(x:x,y:y,width:320,height:480))!
        let out = root.appendingPathComponent("\(frameDirectory)/\(name)-\(frame).png")
        let dest = CGImageDestinationCreateWithURL(out as CFURL, UTType.png.identifier as CFString, 1, nil)!
        CGImageDestinationAddImage(dest,tile,nil)
        precondition(CGImageDestinationFinalize(dest))
    }
}
print("Exported 16 frames, 320x480 each")
