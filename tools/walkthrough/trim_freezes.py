"""Shorten every frozen stretch of a screen recording to at most KEEP seconds.

A walkthrough pauses on each caption for reading time, but the browser tool's own overhead
(page snapshots, sign-ins) adds still frames on top. Any stretch frozen longer than KEEP keeps
its first KEEP seconds; the rest is cut. Usage: trim_freezes.py in.mp4 out.mp4 [KEEP]
"""
import re, subprocess, sys

src, out = sys.argv[1], sys.argv[2]
keep = float(sys.argv[3]) if len(sys.argv) > 3 else 8.5
log = subprocess.run(["ffmpeg", "-i", src, "-vf", f"freezedetect=n=0.002:d={keep}", "-map", "0:v", "-f", "null", "-"],
                     capture_output=True, text=True).stderr
starts = [float(x) for x in re.findall(r"freeze_start: ([\d.]+)", log)]
ends = [float(x) for x in re.findall(r"freeze_end: ([\d.]+)", log)]
cuts = [(s + keep, e) for s, e in zip(starts, ends) if e - s > keep]
drop = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in cuts) or "0"
subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src, "-vf", f"select='not({drop})',setpts=N/30/TB", "-r", "30",
                "-c:v", "libx264", "-preset", "slow", "-crf", "26", "-pix_fmt", "yuv420p", "-an", out], check=True)
print(f"cut {sum(b - a for a, b in cuts):.0f} s from {len(cuts)} frozen stretches")
