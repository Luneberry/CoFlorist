import Foundation
import CoreImage
import Vision
let payload = "WIFI:T:WPA;S:Pusheen-Cat-Setup;P:12345678;;"
let filter = CIFilter(name:"CIQRCodeGenerator")!
filter.setValue(payload.data(using:.utf8)!,forKey:"inputMessage")
filter.setValue("M",forKey:"inputCorrectionLevel")
let output=filter.outputImage!
let context=CIContext()
let w=Int(output.extent.width)
var pixels=[UInt8](repeating:0,count:w*w*4)
context.render(output,toBitmap:&pixels,rowBytes:w*4,bounds:output.extent,format:.RGBA8,colorSpace:CGColorSpaceCreateDeviceRGB())
let cg=context.createCGImage(output.transformed(by:CGAffineTransform(scaleX:8,y:8)),from:output.extent.applying(CGAffineTransform(scaleX:8,y:8)))!
let request=VNDetectBarcodesRequest()
try VNImageRequestHandler(cgImage:cg).perform([request])
assert(request.results?.contains(where:{$0.payloadStringValue==payload})==true)
// Preserve bitmap row order so finder patterns sit at top-left, top-right, bottom-left.
var bits: [String] = []
for y in 0..<w { for x in 0..<w { bits.append(pixels[(y*w+x)*4] < 128 ? "1" : "0") } }
let header="#pragma once\n#define CAT_QR_SIZE \(w)\nstatic const unsigned char cat_setup_qr[\(w*w)] = {\n"+bits.joined(separator:",")+"\n};\n"
try header.write(toFile:CommandLine.arguments[1],atomically:true,encoding:.utf8)
print("Setup QR generated and payload verified; modules \(w)x\(w)")
let edge=(w+8)*5
var lcd=[UInt8](repeating:255,count:edge*edge)
for y in 0..<w { for x in 0..<w { for dy in 0..<5 { for dx in 0..<5 {
    lcd[((y+4)*5+dy)*edge+(x+4)*5+dx] = bits[y*w+x] == "1" ? 0:255
}}}}
let data=Data(lcd) as CFData
let provider=CGDataProvider(data:data)!
let actual=CGImage(width:edge,height:edge,bitsPerComponent:8,bitsPerPixel:8,bytesPerRow:edge,space:CGColorSpaceCreateDeviceGray(),bitmapInfo:CGBitmapInfo(rawValue:0),provider:provider,decode:nil,shouldInterpolate:false,intent:.defaultIntent)!
let check=VNDetectBarcodesRequest()
try VNImageRequestHandler(cgImage:actual).perform([check])
assert(check.results?.contains(where:{$0.payloadStringValue==payload})==true)
print("Actual LCD module layout decoded successfully")
