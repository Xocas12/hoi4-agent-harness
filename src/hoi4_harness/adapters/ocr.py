"""Text from the screen, with where it is: Windows' own OCR, through PowerShell.

Why this and not a dependency: ``Windows.Media.Ocr`` (WinRT) ships with Windows 10 and 11, and
Windows PowerShell 5.1 can call it, so nothing is added to the Python install. The cost is a
PowerShell start per read (about a second), which an event window can afford: it is read once per
turn, not per frame.

The wrapper is a function from a screen rectangle to ``OcrPage``: the lines found, each with its
box in SCREEN pixels. The real backend (``WindowsOcr``) captures the rectangle itself
(``System.Drawing``, per-monitor DPI aware so the pixels are the game's), upscales it, reads it,
and prints JSON that ``parse_output`` turns into a page. Tests inject a fake backend with the same
two methods; nothing here sends input, and the only thing it ever captures is the rectangle it is
asked for.

``available()`` says why OCR cannot run (not Windows, no PowerShell, no OCR language installed)
as a sentence a person can act on, or None when it can. It never raises: a missing OCR is a
degraded run, reported, not a crash.

UNVERIFIED live: that the game's typewriter fonts at the window's size read well. Checked offline
on a synthetic image only (tests/test_ocr.py, a Windows-only test).
"""

# ruff: noqa: E501  (the embedded PowerShell script has long lines)
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

#: Upscaling before recognition: the engine reads small type badly, the game's body text is
#: about 16 px at 1080p.
DEFAULT_UPSCALE = 2.0


@dataclass(frozen=True)
class OcrLine:
    text: str
    x: int
    y: int
    w: int
    h: int

    @property
    def centre(self) -> tuple[int, int]:
        return (self.x + self.w // 2, self.y + self.h // 2)

    @property
    def bottom(self) -> int:
        return self.y + self.h


@dataclass(frozen=True)
class OcrPage:
    """What one read found. ``rect`` is the screen rectangle that was read."""
    lines: tuple[OcrLine, ...] = ()
    rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    language: str = ""

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines)


class OcrBackend(Protocol):
    def available(self) -> str | None:
        """None when this backend can read; else why it cannot, as one sentence."""

    def read(self, rect: tuple[int, int, int, int]) -> OcrPage:
        """Read the screen rectangle ``(x, y, width, height)``; lines are in screen pixels."""


class OcrUnavailable(RuntimeError):
    """OCR cannot run here (the message says why and what to do)."""


def parse_output(raw: str, rect: tuple[int, int, int, int]) -> OcrPage:
    """The JSON the PowerShell script prints -> a page in screen pixels.

    ``{"language": "en-US", "scale": 2, "lines": [{"text", "x", "y", "w", "h"}]}`` where the
    boxes are in the (upscaled) image's pixels; they are divided by ``scale`` and moved by the
    rectangle's origin. ``{"error": "..."}`` raises OcrUnavailable."""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise OcrUnavailable(f"the OCR script printed something that is not JSON ({exc}): "
                             f"{str(raw)[:200]!r}") from None
    if not isinstance(data, dict):
        raise OcrUnavailable("the OCR script printed JSON that is not an object")
    if data.get("error"):
        raise OcrUnavailable(str(data["error"]))
    scale = float(data.get("scale") or 1.0) or 1.0
    lines = []
    for row in data.get("lines") or []:
        try:
            text = str(row["text"]).strip()
            box = [float(row[k]) / scale for k in ("x", "y", "w", "h")]
        except (KeyError, TypeError, ValueError):
            continue
        if text:
            lines.append(OcrLine(text, round(rect[0] + box[0]), round(rect[1] + box[1]),
                                 round(box[2]), round(box[3])))
    return OcrPage(tuple(lines), tuple(rect), str(data.get("language", "")))


#: The script. Inputs come through the environment, so no argument can be mistaken for code:
#: HOI4_OCR_IMAGE (a PNG to read) or HOI4_OCR_RECT ("x,y,w,h": a screen rectangle to capture),
#: HOI4_OCR_SCALE (upscale), HOI4_OCR_PROBE=1 (only report whether an engine exists).
_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
function Out-Json($o) { [Console]::Out.Write(($o | ConvertTo-Json -Depth 5 -Compress)) }
try {
  Add-Type -AssemblyName System.Runtime.WindowsRuntime
  Add-Type -AssemblyName System.Drawing
  $null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
  $null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType = WindowsRuntime]
  $null = [Windows.Storage.StorageFile, Windows.Foundation, ContentType = WindowsRuntime]
  $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
  if ($null -eq $engine) {
    Out-Json @{ error = 'Windows has no OCR language for the user profile languages: add the English (US) language pack with its Optical character recognition feature (Settings, Time and language, Language and region)' }
    exit 0
  }
  if ($env:HOI4_OCR_PROBE -eq '1') { Out-Json @{ language = $engine.RecognizerLanguage.LanguageTag; scale = 1; lines = @() }; exit 0 }
  $asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
  function Await($op, $type) {
    $task = $asTask.MakeGenericMethod($type).Invoke($null, @($op)); $null = $task.Wait(-1); $task.Result
  }
  $scale = [double]$env:HOI4_OCR_SCALE; if ($scale -lt 1) { $scale = 1 }
  $path = $env:HOI4_OCR_IMAGE
  $temp = $null
  if ($env:HOI4_OCR_RECT) {
    Add-Type -TypeDefinition 'using System.Runtime.InteropServices; public static class Dpi { [DllImport("user32.dll")] public static extern bool SetProcessDPIAware(); }'
    $null = [Dpi]::SetProcessDPIAware()
    $r = $env:HOI4_OCR_RECT.Split(',') | ForEach-Object { [int]$_ }
    $shot = New-Object System.Drawing.Bitmap $r[2], $r[3]
    $g = [System.Drawing.Graphics]::FromImage($shot)
    $g.CopyFromScreen($r[0], $r[1], 0, 0, $shot.Size)
    $g.Dispose()
    $src = $shot
  } else {
    $src = [System.Drawing.Image]::FromFile($path)
  }
  $w = [int]($src.Width * $scale); $h = [int]($src.Height * $scale)
  $big = New-Object System.Drawing.Bitmap $w, $h
  $g = [System.Drawing.Graphics]::FromImage($big)
  $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
  $g.DrawImage($src, 0, 0, $w, $h)
  $g.Dispose(); $src.Dispose()
  $temp = [System.IO.Path]::Combine([System.IO.Path]::GetTempPath(), 'hoi4_ocr_' + [guid]::NewGuid().ToString('N') + '.png')
  $big.Save($temp, [System.Drawing.Imaging.ImageFormat]::Png); $big.Dispose()
  try {
    $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($temp)) ([Windows.Storage.StorageFile])
    $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    $result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
  } finally { Remove-Item -LiteralPath $temp -ErrorAction SilentlyContinue }
  $rows = @()
  foreach ($line in $result.Lines) {
    $x0 = [double]::MaxValue; $y0 = [double]::MaxValue; $x1 = 0; $y1 = 0
    foreach ($word in $line.Words) {
      $b = $word.BoundingRect
      if ($b.X -lt $x0) { $x0 = $b.X }; if ($b.Y -lt $y0) { $y0 = $b.Y }
      if ($b.X + $b.Width -gt $x1) { $x1 = $b.X + $b.Width }; if ($b.Y + $b.Height -gt $y1) { $y1 = $b.Y + $b.Height }
    }
    if ($line.Words.Count -gt 0) {
      $rows += @{ text = $line.Text; x = $x0; y = $y0; w = ($x1 - $x0); h = ($y1 - $y0) }
    }
  }
  Out-Json @{ language = $engine.RecognizerLanguage.LanguageTag; scale = $scale; lines = $rows }
} catch {
  Out-Json @{ error = ('OCR failed: ' + $_.Exception.Message) }
}
"""


def _encoded() -> str:
    return base64.b64encode(_SCRIPT.encode("utf-16-le")).decode("ascii")


class WindowsOcr:
    """The WinRT OCR engine through ``powershell.exe`` (Windows PowerShell 5.1)."""

    def __init__(self, *, upscale: float = DEFAULT_UPSCALE, timeout: float = 30.0,
                 runner=None, platform: str | None = None):
        self.upscale = upscale
        self.timeout = timeout
        self._run = runner or subprocess.run
        self._platform = platform if platform is not None else sys.platform
        self._why: str | None | bool = False        # False: not asked yet

    def _call(self, env_extra: dict[str, str]) -> str:
        env = {**os.environ, **env_extra}
        try:
            done = self._run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-EncodedCommand", _encoded()],
                capture_output=True, text=True, timeout=self.timeout, env=env)
        except FileNotFoundError:
            raise OcrUnavailable("powershell.exe was not found, so Windows OCR cannot run") from None
        except subprocess.TimeoutExpired:
            raise OcrUnavailable(f"the OCR script did not finish in {self.timeout:g} s") from None
        except OSError as exc:
            raise OcrUnavailable(f"powershell.exe could not be started ({exc})") from None
        if not (done.stdout or "").strip():
            raise OcrUnavailable("the OCR script printed nothing"
                                 + (f": {(done.stderr or '').strip()[:300]}" if done.stderr else ""))
        return done.stdout

    def available(self) -> str | None:
        if self._why is not False:
            return self._why                       # type: ignore[return-value]
        if self._platform != "win32":
            self._why = f"Windows OCR needs Windows (this is {self._platform})"
            return self._why
        try:
            parse_output(self._call({"HOI4_OCR_PROBE": "1"}), (0, 0, 0, 0))
            self._why = None
        except OcrUnavailable as exc:
            self._why = str(exc)
        return self._why

    def read(self, rect: tuple[int, int, int, int]) -> OcrPage:
        why = self.available()
        if why:
            raise OcrUnavailable(why)
        x, y, w, h = (int(v) for v in rect)
        if w <= 0 or h <= 0:
            raise OcrUnavailable(f"the rectangle to read is empty ({w}x{h})")
        raw = self._call({"HOI4_OCR_RECT": f"{x},{y},{w},{h}",
                          "HOI4_OCR_SCALE": f"{self.upscale:g}"})
        return parse_output(raw, (x, y, w, h))

    def read_image(self, path: Path | str) -> OcrPage:
        """Read an image file (a test's synthetic image); boxes are in the image's pixels."""
        why = self.available()
        if why:
            raise OcrUnavailable(why)
        raw = self._call({"HOI4_OCR_IMAGE": str(path), "HOI4_OCR_SCALE": f"{self.upscale:g}"})
        return parse_output(raw, (0, 0, 0, 0))


@dataclass
class FakeOcr:
    """A backend for tests: ``source(rect)`` returns the lines the 'screen' shows."""
    source: object
    why: str | None = None
    reads: list = field(default_factory=list)

    def available(self) -> str | None:
        return self.why

    def read(self, rect):
        if self.why:
            raise OcrUnavailable(self.why)
        self.reads.append(tuple(rect))
        lines = self.source(rect)
        return OcrPage(tuple(lines), tuple(rect), "fake")
