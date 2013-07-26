#!/usr/bin/python3
#
# This script will generate Kicad modules (footprints) for QFP (TQFP, LQFP, ...) packages
# using the pad margins defined in IPC 7351. The footprint geometry is implicit from its
# IPC 7351 name, except for the terminal (lead) width that has to be specified separately.
#
# Limitations:
#   * Only square packages are implemented (there are rectangular ones in JEDEC MS-026).
#   * I haven't seen IPC-7351A or IPC-7351B.
#
# Some package examples:
#
# Atmel Atmega8 package 32A (TQFP-32, 0.8mm pitch):
#   body size 7x7 mm, conforms to JEDEC MS-026 variation ABA.
#   Terminal (lead) width is 0.45mm (max) according to JEDEC.
#   -n QFP80P900X900X100-32 -W 0.45
#
# STM32F102x8: LQFP-48 0.5mm pitch:
# TI TLK110: PT (S-PQFP-G48) package, 0.5mm pitch:
#   body size 7x7 mm, no JEDEC standard mentioned.
#   -n QFP50P900X900X100-48 -W 0.27
#
# STM32F102x8: LQFP-64 0.5mm pitch:
#   body size 10x10 mm, no JEDEC standard mentioned.
#   -n QFP50P1200X1200X100-64 -W 0.27
#
# JEDEC MS-026D variation BJC (256 pins)
#   body size 28x28 mm, pitch 0.4 mm
#   -n QFP40P3000X3000-256 -W 0.23
#

import optparse
import math
import re
import sys
import time
import cairo

def rotate(p, angle):
    """Rotate vector, compensating for the Y axis being upside down"""
    th = math.radians(angle)
    return (math.cos(th)*p[0] + math.sin(th)*p[1], -(math.sin(th)*p[0] - math.cos(th)*p[1]))

class Line:
    def __init__(self, start, end):
        self.layer = "F.SilkS"
        self.start = start
        self.end = end

    def rotate(self, th):
        self.start = rotate(self.start, th)
        self.end = rotate(self.end, th)

    def kicad_sexp(self):
        return "  (fp_line (start %.3f %.3f) (end %.3f %.3f) (layer %s) (width %.2f))\n" % (
            self.start[0], self.start[1],
            self.end[0], self.end[1],
            self.layer, self.width)

    def draw(self, ctx):
        ctx.set_source_rgb(0, 0.52, 0.52)
        ctx.set_line_width(self.width)
        ctx.move_to(*self.start)
        ctx.line_to(*self.end)
        ctx.stroke()

class Circle:
    def __init__(self, pos, size):
        self.layer = "F.SilkS"
        self.pos = pos
        self.size = size

    def kicad_sexp(self):
        return "  (fp_circle (center %.2f %.2f) (end %.2f %.2f) (layer %s) (width %.2f))\n" % (
            self.pos[0], self.pos[1],
            self.pos[0] + self.size, self.pos[1],
            self.layer, self.width)

    def draw(self, ctx):
        ctx.set_source_rgb(0, 0.52, 0.52)
        ctx.set_line_width(self.width)
        ctx.arc(self.pos[0], self.pos[1], self.size, 0, 2*math.pi)
        ctx.stroke()

class Pad:
    def __init__(self, number):
        self.number = number
        self.rotation = 0

    def rotate(self, th):
        self.rotation += th
        (self.x, self.y) = rotate((self.x, self.y), th)

    def kicad_sexp(self):
        return "  (pad %d smd rect (at %.2f %.2f %.0f) (size %.2f %.2f) (layers F.Cu F.Paste F.Mask))\n" % (
            self.number,
            self.x, self.y,
            self.rotation,
            self.xsize,
            self.ysize)

    def draw(self, ctx):
        ctx.save()
        ctx.set_source_rgb(0.52, 0, 0)
        ctx.translate(self.x, self.y)
        ctx.rotate(math.radians(self.rotation))
        ctx.rectangle(-self.xsize/2, -self.ysize/2, self.xsize, self.ysize)
        ctx.fill()
        ctx.restore()

class Params:
    # All measurements are floating-point mm
    density = None
    JT = 0 # solder fillet or land protrusion at toe
    JH = 0 # solder fillet or land protrusion at heel
    JS = 0 # solder fillet or land protrusion at side
    l1 = 0 # package toe-to-toe size X dimension
    l2 = 0 # package toe-to-toe size Y dimension
    termlen = 0 # terminal (lead) length. The heal-to-toe length of the terminal
    termwidth = 0 # terminal (lead) width.

    def kicad_sexp(self):
        s = ""
        s += "#%12s: %8d\n" % ("pins", self.pincount)
        for p in ("pitch", "l1", "l2", "JT", "JH", "JS", "termlen", "termwidth"):
            s += "#%12s: %8.2f mm  (%8.1f mil)\n" % (p, self.__dict__[p], self.__dict__[p] / .0254)
        return s


def parse_qfp_name(name):
    """Parse IPC name (like QFP50P900X900-48) and return data in mm"""

    match = re.match("QFP(\d+)P(\d+)X(\d+)(X\d+)?-(\d+)(.)?", name)

    if match is None:
        return None

    p = Params()
    p.pitch = int(match.group(1)) / 100
    p.l1 = int(match.group(2)) / 100
    p.l2 = int(match.group(3)) / 100
    p.pincount = int(match.group(5))
    print(match.group(6))
    if match.group(6) is not None:
        p.density = match.group(6)
    return p

def make_qfp_package(params):
    package = []

    l = params.l1 # Package length along this dimension
    # Positions of things relative to package center
    padtoe = l / 2 + params.JT
    padheel = l / 2 - params.termlen - params.JH
    padlen = padtoe - padheel
    padcenter = padtoe - padlen / 2
    padwidth = params.termwidth + params.JS
    pins_per_side = params.pincount // 4

    if params.draw_outline:
        # Draw inside placement guide on silkscreen
        for side in range(0, 4):
            rectsize = padheel - 0.25
            th = (270 + side * 90) % 360 # Coordinate system rotation for this side
            line = Line( (rectsize, rectsize), (rectsize, -rectsize) )
            line.width = params.linewidth
            line.rotate(th)
            package.append(line)

        # Draw inside orientation mark on silkscreen
        circle = Circle( (-(padheel - 1.25), padheel - 1.25), 0.5)
        circle.width = params.linewidth
        package.append(circle)

    if params.draw_courtyard:
        # Draw courtyard on silkscreen
        courtyardsize = padtoe + params.courtyard_excess
        for side in range(0, 4):    
            th = (270 + side * 90) % 360 # Coordinate system rotation for this side
            line = Line( (courtyardsize, courtyardsize), (courtyardsize, -courtyardsize) )
            line.width = params.linewidth
            line.rotate(th)
            package.append(line)

        # Draw orientation mark on the courtyard
        marksize = 1.5
        line = Line( (-courtyardsize, courtyardsize - marksize), (-courtyardsize + marksize, courtyardsize) )
        line.width = params.linewidth
        package.append(line)

    if params.draw_terminals:
        # Draw terminals/"feet"
        pinno = 1
        for side in range(0, 4):
            th = (270 + side * 90) % 360 # Coordinate system rotation for this side

            toe = l / 2
            heel = toe - params.termlen
            width = params.termwidth

            # Terminal center coordinates
            y = (pins_per_side - 1) * params.pitch / 2

            lines = []
            for pin in range(0, pins_per_side):
                lines.append(Line( (heel, y + width/2), (toe, y + width/2) ))
                lines.append(Line( (toe, y + width/2),  (toe, y - width/2) ))
                lines.append(Line( (toe, y - width/2),  (heel, y - width/2) ))
                lines.append(Line( (heel, y - width/2),  (heel, y + width/2) ))
                y -= params.pitch

            for line in lines:
                line.layer = "Dwgs.User"
                line.width = 0.02
                line.rotate(th)
                package.append(line)

    # Add pads, starting with pin 1 in lower-left (negative X, positive Y) corner
    # Pads are drawn on the 0-degree (right) side and rotated into place
    pinno = 1
    for side in range(0, 4):
        th = (270 + side * 90) % 360 # Coordinate system rotation for this side

        # Pad center coordinates
        x = padcenter
        y = (pins_per_side - 1) * params.pitch / 2

        for pin in range(0, pins_per_side):
            pad = Pad(pinno)
            pad.x = x
            pad.y = y
            pad.ysize = padwidth
            pad.xsize = padlen
            pad.rotate(th)

            package.append(pad)

            pinno += 1
            y -= params.pitch

    return package

description="""Generate a QFP footprint (land pattern) from an IPC name.
The name is given on the form QFP<pitch>P<L1>X<L2>[X<height>]-<pincount>, where
pitch is the distance between the centre of the pins; L1 and L2 is the nominal
width in the X and Y dimensions of the package measured between opposite pin toes;
height is optionally the thickness of the package (ignored); pincount is the number
of pins (leads) on the package. The unit for all measurements is mm, represented in
1/100ths. For example: QFP50P900X900-48 is a square QFP package with 48 pins where
the distance between the pin ends is 9.00 mm, pin pitch 0.50 mm (this is a standard
7x7 mm LQFP package).
"""

if __name__ == "__main__":
    # Parse command line
    parser = optparse.OptionParser(usage="Usage: %prog [options]", description=description)
    parser.add_option("-n", dest="name",
                      help="IPC device name and description string. For example QFP50P900X900-48", metavar="IPCNAME")
    parser.add_option("-f", dest="format", default="kicad_mod",
                      help="Output file format: kicad_mod, png", metavar="FORMAT")
    parser.add_option("-o", dest="outfile", default="out",
                      help="Output file name", metavar="FILE")
    parser.add_option("-T", dest="termlen", type="float", default="0.6",
                      help="Nominal terminal (lead) length (heel-to-toe), in floating-point mm", metavar="N")
    parser.add_option("-W", dest="termwidth", type="float", default="0.27",
                      help="Maximum terminal (lead) width, in floating-point mm", metavar="N")
    parser.add_option("-D", "--density", dest="density", default="N",
                      help="IPC-7351 density level: L (least), N (nominal), M (most)")
    parser.add_option("--toe-protrusion", dest="jt", type="float",
                      help="Override toe protrusion (outside pad length), in floating-point mm", metavar="N")

    group = optparse.OptionGroup(parser, "Silkscreen options")
    group.add_option("--draw-outline", dest="draw_outline", action="store_true",
                     help="Draw package outline and orientation mark (inside pads) on silkscreen)")
    group.add_option("--draw-courtyard", dest="draw_courtyard", action="store_true",
                     help="Draw courtyard and orientation mark (outside pads) on silkscreen")
    group.add_option("--draw-terminals", dest="draw_terminals", action="store_true",
                     help="Draw terminal (pin) outlines on drawing layer (Dwgs.User)")
    parser.add_option_group(group)

    group = optparse.OptionGroup(parser, "Image output options")
    group.add_option("--scale", dest="pngscale", type="int", default="8",
                     help="Image scale in number of pixels per mm", metavar="N")
    parser.add_option_group(group)

    (options, args) = parser.parse_args()

    if not options.name:
        parser.error("-n argument is mandatory")

    params = parse_qfp_name(options.name)
    if params.density is None:
        params.density = options.density
    params.termlen = options.termlen
    params.termwidth = options.termwidth
    params.linewidth = 0.15
    params.draw_courtyard = options.draw_courtyard
    params.draw_outline = options.draw_outline
    params.draw_terminals = options.draw_terminals

    if params.density == "L":
        params.JT = 0.15
        params.JH = 0.25
        if params.pitch > 0.625:
            params.JS = 0.01
        else:
            params.JS = -0.04
        params.courtyard_excess = 0.10
    elif params.density == "N":
        params.JT = 0.35
        params.JH = 0.35
        if params.pitch > 0.625:
            params.JS = 0.03
        else:
            params.JS = -0.02
        params.courtyard_excess = 0.25
    elif params.density == "M":
        params.JT = 0.55
        params.JH = 0.45
        if params.pitch > 0.625:
            params.JS = 0.05
        else:
            params.JS = 0.01
        params.courtyard_excess = 0.50
    else:
        parser.error("Invalid density (need L,N or M)")

    if options.jt:
        params.JT = options.jt

    package = make_qfp_package(params)

    if options.format == "kicad_mod":
        f = open(options.outfile, "w")
        f.write("# This footprint was generated by on %s using the command\n" % time.asctime())
        f.write("# %s\n" % (" ".join(sys.argv)))
        f.write("# Footprint parameters:\n")
        f.write(params.kicad_sexp())

        f.write("(module %s (layer F.Cu)\n" % options.name)
        f.write("  (at 0 0)\n")
        f.write("  (fp_text reference %s (at 0 -1) (layer F.SilkS)\n" % options.name)
        f.write("    (effects (font (size 1.5 1.5) (thickness 0.15))))\n")
        f.write("  (fp_text value VAL** (at 0 1) (layer F.SilkS) hide\n")
        f.write("    (effects (font (size 1.5 1.5) (thickness 0.15))))\n")
        for d in package:
            f.write(d.kicad_sexp())
        f.write(")\n") # close module

    elif options.format == "png":
        scale = float(options.pngscale)
        margin = 0.1 # mm
        w = int((params.l1/2 + params.JT + params.courtyard_excess + margin) * 2 * scale)
        h = int((params.l2/2 + params.JT + params.courtyard_excess + margin) * 2 * scale)
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
        ctx = cairo.Context(surface)

        ctx.set_line_cap(cairo.LINE_CAP_ROUND)

        # Move origin to center of image, scale to mm
        ctx.translate(w/2, h/2)
        ctx.scale(scale, scale)

        for d in package:
            d.draw(ctx)

        surface.write_to_png(options.outfile)
