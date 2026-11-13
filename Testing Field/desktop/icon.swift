import AppKit

let canvas = NSSize(width: 1024, height: 1024)
let image = NSImage(size: canvas, flipped: false) { _ in
    let tile = NSBezierPath(roundedRect: NSRect(x: 100, y: 100, width: 824, height: 824),
                            xRadius: 198, yRadius: 198)
    let colors = [NSColor(srgbRed: 0.46, green: 0.33, blue: 0.95, alpha: 1),
                  NSColor(srgbRed: 0.28, green: 0.17, blue: 0.75, alpha: 1)]
    NSGradient(colors: colors)?.draw(in: tile, angle: -65)

    NSColor.white.withAlphaComponent(0.14).setFill()
    NSBezierPath(roundedRect: NSRect(x: 231, y: 294, width: 562, height: 436),
                 xRadius: 111, yRadius: 111).fill()

    let waveform: [(CGFloat, CGFloat)] = [(326, 80), (419, 172), (512, 252), (605, 160), (698, 86)]
    NSColor.white.setFill()
    for (center, height) in waveform {
        NSBezierPath(roundedRect: NSRect(x: center - 25, y: 512 - height / 2, width: 50, height: height),
                     xRadius: 25, yRadius: 25).fill()
    }
    return true
}

guard CommandLine.arguments.count == 2,
      let tiff = image.tiffRepresentation,
      let bitmap = NSBitmapImageRep(data: tiff),
      let png = bitmap.representation(using: .png, properties: [:]) else {
    fatalError("Usage: swift icon.swift <output.png>")
}
try png.write(to: URL(fileURLWithPath: CommandLine.arguments[1]))
