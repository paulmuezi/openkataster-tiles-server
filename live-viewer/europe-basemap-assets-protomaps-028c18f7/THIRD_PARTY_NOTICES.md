# Third-party notices

This directory contains a pinned copy of `protomaps/basemaps-assets` at commit
`028c18f713baecad011301ff7a69acc39bcc2ae7`.

Upstream archive:
`https://github.com/protomaps/basemaps-assets/archive/028c18f713baecad011301ff7a69acc39bcc2ae7.tar.gz`

Archive SHA-256:
`c02634724bee074ac41f1bbccc7eecaa3268703d5c601d4c8f3ad0d6bb6378c7`

## Fonts

The bundled Noto Sans glyphs are licensed under the SIL Open Font License 1.1.
The complete license text is included as `fonts/OFL.txt`.

## Sprites

The sprites are derived from `tangrams/icons`, licensed under the MIT License:

Copyright (c) 2017 Mapzen

Permission is hereby granted, free of charge, to any person obtaining a copy of
this software and associated documentation files (the "Software"), to deal in
the Software without restriction, including without limitation the rights to
use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of
the Software, and to permit persons to whom the Software is furnished to do so,
subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS
FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR
COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER
IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

Source: https://github.com/tangrams/icons

## Style generator

The generated style uses `@protomaps/basemaps` 5.7.2. Its code is licensed
under the BSD 3-Clause License:

Copyright 2019-2024 Protomaps LLC

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.
2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.
3. Neither the name of the copyright holder nor the names of its contributors
   may be used to endorse or promote products derived from this software
   without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR
ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON
ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

Source: https://github.com/protomaps/basemaps

## Map data

The Europe basemap tiles are a Produced Work derived from OpenStreetMap data.
They are distributed under the Open Database License 1.0 attribution
requirements. OpenKataster displays a persistent linked
`© OpenStreetMap contributors` credit whenever this basemap is active.

The distributed PMTiles also contain the unrendered `landcover` vector layer
from Daylight Landcover / Overture Maps, derived from ESA WorldCover 2020 and
licensed under CC BY 4.0. Because OpenKataster serves the complete vector-tile
bytes, its runtime manifest, API configuration, style source and source panel
retain the following acknowledgement even though the layer is not rendered:

`© ESA WorldCover project 2020 / Contains modified Copernicus Sentinel data
(2020) processed by ESA WorldCover consortium`

Sources and license:

- https://github.com/protomaps/basemaps/blob/main/LICENSE_DATA.md
- https://docs.overturemaps.org/attribution/
- https://esa-worldcover.org/
- https://creativecommons.org/licenses/by/4.0/
