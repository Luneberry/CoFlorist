import Foundation
import ImageIO
import CoreGraphics
let base=URL(fileURLWithPath:CommandLine.arguments[1])
let frameDirectory=CommandLine.arguments.count > 2 ? CommandLine.arguments[2] : "assets/frames"
let names=["neutral","happy","sad","angry","surprised","thinking","listening","speaking"]
var output="#include <stdint.h>\n#include <stddef.h>\n";var pointers=[String]();var sizes=[String]();var total=0
for name in names {for frame in 0..<2 {
 let url=base.appendingPathComponent("\(frameDirectory)/\(name)-\(frame).png")
 let src=CGImageSourceCreateWithURL(url as CFURL,nil)!
 let img=CGImageSourceCreateImageAtIndex(src,0,nil)!
 var pixels=[UInt8](repeating:0,count:320*480*4)
 pixels.withUnsafeMutableBytes { buf in
  let ctx=CGContext(data:buf.baseAddress,width:320,height:480,bitsPerComponent:8,bytesPerRow:1280,space:CGColorSpaceCreateDeviceRGB(),bitmapInfo:CGImageAlphaInfo.premultipliedLast.rawValue)!
  ctx.draw(img,in:CGRect(x:0,y:0,width:320,height:480))
 }
 var runs=[UInt16]();var previous:UInt16=0;var count:UInt16=0
 for i in stride(from:0,to:pixels.count,by:4) {
  let color=(UInt16(pixels[i]>>3)<<11)|(UInt16(pixels[i+1]>>2)<<5)|UInt16(pixels[i+2]>>3)
  if count>0 && (color != previous || count==65535) {runs += [count,previous];count=0}
  previous=color;count += 1
 }
 if count>0 {runs += [count,previous]}
 var packed=[UInt8]()
 for i in stride(from:0,to:runs.count,by:2){var n=Int(runs[i]);let color=runs[i+1];while n>0{let count=min(n,255);packed += [UInt8(count),UInt8(color&255),UInt8(color>>8)];n -= count}}
 let symbol="cat_\(name)_\(frame)"
 output += "static const uint8_t \(symbol)[]={\n"
 for i in stride(from:0,to:packed.count,by:24){output += packed[i..<min(i+24,packed.count)].map{String($0)}.joined(separator:",")+",\n"}
 output += "};\n";pointers.append(symbol);sizes.append(String(packed.count));total += packed.count
}}
output += "const uint8_t *const cat_frames[16]={"+pointers.joined(separator:",")+"};\nconst size_t cat_frame_bytes[16]={"+sizes.joined(separator:",")+"};\n"
try output.write(to:base.appendingPathComponent("TuyaOpen/apps/pusheen_cat/src/cat_assets.c"),atomically:true,encoding:.utf8)
print("RGB565 RLE payload: \(total) bytes")
