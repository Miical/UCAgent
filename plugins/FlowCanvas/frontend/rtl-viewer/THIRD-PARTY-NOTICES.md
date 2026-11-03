# Third-party notices — rtl-buddy-sch

`rtl-buddy-sch` is licensed under the BSD 3-Clause License (see
[`LICENSE`](LICENSE)). This file documents the third-party software
**redistributed inside the published wheel**, together with the
notices those licenses require us to pass on.

> **First wheel to carry this file: v0.8.1.** The v0.8.0 release was
> built from the commit merged one position *before* this file landed
> (the stack #162→#164→#172 merged within the same second, and the
> release workflow builds from the labeled PR's own merge commit, not
> from the final stack tip), so its `.dist-info/licenses/` contains
> only `LICENSE`. The v0.8.0 *code* is unaffected; v0.8.1 exists to
> put these notices into the artefact, as EPL-2.0 §3 expects.

## What this file covers (and what it does not)

The wheel built from this repository contains exactly two things
(`[tool.hatch.build.targets.wheel]` in `pyproject.toml`):

1. the Python package `src/rtl_buddy_view/` — first-party,
   BSD 3-Clause; and
2. `src/rtl_buddy_view/_viewer_bundle/` — the compiled Vue 3 single-page
   viewer, staged from `viewer/dist/` by `scripts/prebuild_viewer.py`
   and declared as a build artifact so hatchling keeps it despite the
   `.gitignore` entry.

Everything below concerns **(2)**. The Vite build inlines its runtime
npm dependencies into `_viewer_bundle/assets/*.js`, so that file — and
therefore the wheel, and therefore every install of `rtl-buddy-sch` —
is a redistribution of those components in object form.

Explicitly **out of scope**, because none of it ships in the wheel:

- **Python runtime dependencies** (`typer`, `jsonschema`, and the
  optional `pyslang` extra). These are declared requirements that the
  installer fetches separately; we never redistribute their code.
- **Development and build tooling** — Vite, Vitest, Playwright,
  `@vitejs/plugin-vue`, happy-dom, ruff, mypy, pytest, hatchling. These
  are `devDependencies` / dependency-groups; their code is not present
  in `viewer/dist/`.
- **Verible.** `src/rtl_buddy_view/_verible_install.py` *downloads* a
  pinned Verible release into `vendor/verible/` at setup time.
  `vendor/` is neither packaged nor referenced by the wheel build, so
  no Verible binary is redistributed by this project. (Verible itself
  is Apache-2.0; consult its own release for its notices.)
- **`viewer/src/theme.css`**, a byte-for-byte vendored copy of the
  design-token sheet from the sibling `rtl-buddy/rtl_buddy` repository —
  same project family, same authorship, not a third-party component.

## Components redistributed in the viewer bundle

Versions are those pinned by `viewer/package-lock.json` and confirmed
against the module list in the Vite build's source map — i.e. the code
that is actually emitted into the bundle, not merely the declared
dependency set.

| Component | Version | License | Upstream |
| --- | --- | --- | --- |
| `@viz-js/viz` (JS wrapper) | 3.27.0 | MIT | <https://github.com/mdaines/viz-js> |
| ├─ Graphviz (compiled to WebAssembly) | 14.1.5 | **EPL-2.0** | <https://gitlab.com/graphviz/graphviz> |
| ├─ Expat (XML parser, linked into the WASM build) | 2.8.1 | MIT | <https://github.com/libexpat/libexpat> |
| └─ Emscripten runtime support + musl-derived libc | emsdk 5.0.5 | MIT | <https://github.com/emscripten-core/emscripten> |
| `elkjs` | 0.12.0 | **EPL-2.0** | <https://github.com/kieler/elkjs> |
| Vue | 3.5.34 | MIT | <https://github.com/vuejs/core> |
| Pinia | 2.3.1 | MIT | <https://github.com/vuejs/pinia> |
| Chart.js | 4.5.1 | MIT | <https://github.com/chartjs/Chart.js> |
| `@kurkle/color` | 0.3.4 | MIT | <https://github.com/kurkle/color> |
| `vue-chartjs` | 5.3.3 | MIT | <https://github.com/apertureless/vue-chartjs> |

Vue ships as its published runtime packages — `@vue/runtime-dom`,
`@vue/runtime-core`, `@vue/reactivity` and `@vue/shared`, all at
3.5.34, all MIT, all under the same `vuejs/core` copyright below. The
compiler halves (`@vue/compiler-sfc`, `@babel/parser`, `postcss`,
`magic-string`) run inside Vite at build time and are not emitted into
the bundle.

None of these components has been modified. Each is consumed as its
published, unmodified upstream artifact.

---

## 1. `@viz-js/viz` — two layers, two licenses

This is the entry that matters most, and it is easy to get wrong: the
npm package's `license` field says `MIT`, but that describes only the
thin JavaScript wrapper. The package's `lib/backend.js` is a
WebAssembly build of **Graphviz**, and Graphviz is licensed under the
**Eclipse Public License**. Both layers are redistributed in our wheel;
both are documented here.

### 1a. The wrapper — MIT

```
MIT License

Copyright (c) Michael Daines

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### 1b. The embedded Graphviz — EPL-2.0

`@viz-js/viz@3.27.0` embeds **Graphviz 14.1.5**, compiled to
WebAssembly with Emscripten. The version is stated by the package
itself in `lib/metadata.js` (`graphvizVersion = "14.1.5"`) and by its
SLSA provenance attestation (`lib/provenance.json`), which records the
exact source archive consumed by the build:

```
https://gitlab.com/api/v4/projects/4207231/packages/generic/graphviz-releases/14.1.5/graphviz-14.1.5.tar.gz
sha256 f5ce5b0dcf174817673f3c1e38acf253a73d40b79597c3106b96afc6dac0d70f
```

Graphviz source files carry the notice:

```
Copyright (c) 2011 AT&T Intellectual Property
All rights reserved. This program and the accompanying materials
are made available under the terms of the Eclipse Public License v2.0
which accompanies this distribution, and is available at
https://www.eclipse.org/org/documents/epl-2.0/EPL-2.0.html

Contributors: Details at https://graphviz.org
```

> **Note on the license version.** Graphviz was licensed under
> **EPL-1.0** for most of its history and relicensed to **EPL-2.0** at
> release **14.1.4**; secondary sources still commonly describe it as
> EPL-1.0. The bundled 14.1.5 is EPL-2.0, and the full text is
> reproduced in
> [Appendix A](#appendix-a--eclipse-public-license--v-20).
> `viewer/package-lock.json` has pinned `@viz-js/viz@3.27.0` since the
> SPA landed, so every wheel this project has published carries this
> same Graphviz 14.1.5 under EPL-2.0. Should the pin ever move to a
> `@viz-js/viz` build embedding Graphviz < 14.1.4, the applicable text
> becomes EPL-1.0 (<https://www.eclipse.org/legal/epl-v10.html>) and
> this section must change with it.

**Source availability (EPL-2.0 §3.2).** Graphviz is redistributed here
in Object Code form only, unmodified. Its Source Code is available from
the upstream project at <https://gitlab.com/graphviz/graphviz>, and the
exact release archive at the URL and digest quoted above. The WASM
build recipe and the wrapper's own source are available at
<https://github.com/mdaines/viz-js>. We hold no Graphviz modifications;
nothing in `rtl-buddy-sch` patches Graphviz or its build.

**Relationship to this project's BSD-3 license.** `rtl-buddy-sch` code
uses viz.js only through its published JavaScript API and is a Separate
Module under EPL-2.0 §1 — the EPL's reciprocal obligations attach to the
Graphviz Contribution itself, not to the surrounding work. Distributing
the two together in one wheel is a permitted distribution of the
Program provided these notices and the license text travel with it,
which is what this file is for.

### 1c. Expat — MIT

Graphviz's WASM build links **libexpat 2.8.1** (recorded in the same
provenance attestation) for XML/GraphML input parsing.

```
Copyright (c) 1998-2000 Thai Open Source Software Center Ltd and Clark Cooper
Copyright (c) 2001-2025 Expat maintainers
```

Licensed under the MIT License; the notice text is identical in
substance to §1a above with these copyright lines. Full text:
<https://github.com/libexpat/libexpat/blob/master/expat/COPYING>.

### 1d. Emscripten runtime support — MIT

The WASM module is built with the Emscripten SDK (emsdk 5.0.5, per the
provenance attestation). The generated `backend.js` carries Emscripten
runtime support code and a musl-derived libc.

```
Copyright (c) 2010-2014 Emscripten authors, see AUTHORS file.
```

Emscripten is available under two permissive licenses — the MIT License
and the University of Illinois/NCSA Open Source License; the bundled
musl libc is MIT. Full text:
<https://github.com/emscripten-core/emscripten/blob/main/LICENSE>.

(Note: Graphviz's optional zlib-backed image support is *not* compiled
into this build — the WASM module reports "No libz support" — so no
zlib code is redistributed.)

---

## 2. Vue — MIT

```
Copyright (c) 2018-present, Yuxi (Evan) You
```

Covers `vue@3.5.34` and the `@vue/runtime-dom`, `@vue/runtime-core`,
`@vue/reactivity`, `@vue/shared` packages at the same version. MIT
License, text as in §1a with this copyright line. Full text:
<https://github.com/vuejs/core/blob/main/LICENSE>.

## 3. Pinia — MIT

```
Copyright (c) 2019-present Eduardo San Martin Morote
```

MIT License, text as in §1a with this copyright line. Full text:
<https://github.com/vuejs/pinia/blob/v2/LICENSE>.

## 4. Chart.js — MIT

```
Copyright (c) 2014-2024 Chart.js Contributors
```

MIT License, text as in §1a with this copyright line. Full text:
<https://github.com/chartjs/Chart.js/blob/master/LICENSE.md>.

## 5. `@kurkle/color` — MIT

```
Copyright (c) 2018-2024 Jukka Kurkela
```

Chart.js's colour-manipulation dependency, bundled transitively. MIT
License, text as in §1a with this copyright line. Full text:
<https://github.com/kurkle/color/blob/main/LICENSE.md>.

## 6. `vue-chartjs` — MIT

```
Copyright (c) 2016 Jakub Juszczak
```

MIT License, text as in §1a with this copyright line. Full text:
<https://github.com/apertureless/vue-chartjs/blob/main/LICENSE>.

## 7. `elkjs` — EPL-2.0

`elkjs@0.12.0` is the **Eclipse Layout Kernel (ELK)** — Java code
maintained at <https://github.com/eclipse-elk/elk> — transpiled to
JavaScript with GWT and published from
<https://github.com/kieler/elkjs>. It computes the layered layout
behind the SPA's schematic canvas (`viewer/src/layout/elkSchematic.js`).
Its source headers carry notices of the form:

```
Copyright (c) 2017 Kiel University and others.
```

Its `package.json` declares `EPL-2.0 OR GPL-3.0-or-later` — that is
EPL-2.0 with GPL-3.0-or-later offered as a Secondary License under
EPL-2.0 §3.2. This project receives and redistributes elkjs under
**EPL-2.0**; the GPL option is not exercised. The license text is the
same EPL-2.0 already reproduced in
[Appendix A](#appendix-a--eclipse-public-license--v-20) for Graphviz.

**Source availability (EPL-2.0 §3.2).** elkjs is redistributed inside
the viewer bundle in unmodified form. Its Source Code — the
transpilation project and the upstream Java it is generated from — is
available at the two repositories above, and the exact artifact at
<https://registry.npmjs.org/elkjs/-/elkjs-0.12.0.tgz>. We hold no
elkjs modifications; nothing in `rtl-buddy-sch` patches elkjs or its
build.

**Relationship to this project's BSD-3 license.** As with Graphviz
(§1b), `rtl-buddy-sch` consumes elkjs only through its published
JavaScript API (`new ELK().layout(...)`), so the surrounding code is a
Separate Module under EPL-2.0 §1. The EPL's reciprocal obligations
attach to the ELK Contribution itself, not to this project; shipping
both in one wheel is a permitted distribution provided these notices
and the license text travel with it.

---

## Keeping this file correct

This file is hand-maintained and describes the bundle produced from the
current `viewer/package-lock.json`. Any change that adds, removes, or
version-bumps a **runtime** npm dependency of `viewer/` changes what the
wheel redistributes and must update the table above in the same change
set. `devDependencies` do not.

To re-derive the ground truth after a dependency change:

```bash
npm --prefix viewer ci
npm --prefix viewer run build   # sourcemap names every emitted module
node <<'EOF'
const fs = require('fs'), dir = 'viewer/dist/assets'
const map = fs.readdirSync(dir).find(f => f.endsWith('.js.map'))
const pkgs = new Set()
for (const s of JSON.parse(fs.readFileSync(`${dir}/${map}`, 'utf8')).sources) {
  const i = s.lastIndexOf('node_modules/')
  if (i < 0) continue
  const p = s.slice(i + 13).split('/')
  pkgs.add(p[0][0] === '@' ? `${p[0]}/${p[1]}` : p[0])
}
for (const p of [...pkgs].sort()) {
  console.log(p, require(`./viewer/node_modules/${p}/package.json`).version)
}
EOF
```

Every name it prints must be accounted for in the table above (the
four `@vue/*` runtime packages are covered by the single Vue row);
anything in `viewer/package.json`'s `dependencies` that it does *not*
print was tree-shaken out and does not ship. For a component that embeds
compiled third-party code the way `@viz-js/viz` does, the npm
`license` field is not the whole answer — read the package's own
metadata (`lib/metadata.js`, `lib/provenance.json` in viz.js's case)
before writing the entry.

---

## Appendix A — Eclipse Public License — v 2.0

Applies to the Graphviz code embedded in `@viz-js/viz` (§1b) and to
`elkjs` (§7).
Reproduced verbatim from the `LICENSE` file of the Graphviz 14.1.5
source release named in the provenance attestation above.

```
Eclipse Public License - v 2.0

    THE ACCOMPANYING PROGRAM IS PROVIDED UNDER THE TERMS OF THIS ECLIPSE
    PUBLIC LICENSE ("AGREEMENT"). ANY USE, REPRODUCTION OR DISTRIBUTION
    OF THE PROGRAM CONSTITUTES RECIPIENT'S ACCEPTANCE OF THIS AGREEMENT.

1. DEFINITIONS

"Contribution" means:

  a) in the case of the initial Contributor, the initial content
     Distributed under this Agreement, and

  b) in the case of each subsequent Contributor:
     i) changes to the Program, and
     ii) additions to the Program;
  where such changes and/or additions to the Program originate from
  and are Distributed by that particular Contributor. A Contribution
  "originates" from a Contributor if it was added to the Program by
  such Contributor itself or anyone acting on such Contributor's behalf.
  Contributions do not include changes or additions to the Program that
  are not Modified Works.

"Contributor" means any person or entity that Distributes the Program.

"Licensed Patents" mean patent claims licensable by a Contributor which
are necessarily infringed by the use or sale of its Contribution alone
or when combined with the Program.

"Program" means the Contributions Distributed in accordance with this
Agreement.

"Recipient" means anyone who receives the Program under this Agreement
or any Secondary License (as applicable), including Contributors.

"Derivative Works" shall mean any work, whether in Source Code or other
form, that is based on (or derived from) the Program and for which the
editorial revisions, annotations, elaborations, or other modifications
represent, as a whole, an original work of authorship.

"Modified Works" shall mean any work in Source Code or other form that
results from an addition to, deletion from, or modification of the
contents of the Program, including, for purposes of clarity any new file
in Source Code form that contains any contents of the Program. Modified
Works shall not include works that contain only declarations,
interfaces, types, classes, structures, or files of the Program solely
in each case in order to link to, bind by name, or subclass the Program
or Modified Works thereof.

"Distribute" means the acts of a) distributing or b) making available
in any manner that enables the transfer of a copy.

"Source Code" means the form of a Program preferred for making
modifications, including but not limited to software source code,
documentation source, and configuration files.

"Secondary License" means either the GNU General Public License,
Version 2.0, or any later versions of that license, including any
exceptions or additional permissions as identified by the initial
Contributor.

2. GRANT OF RIGHTS

  a) Subject to the terms of this Agreement, each Contributor hereby
  grants Recipient a non-exclusive, worldwide, royalty-free copyright
  license to reproduce, prepare Derivative Works of, publicly display,
  publicly perform, Distribute and sublicense the Contribution of such
  Contributor, if any, and such Derivative Works.

  b) Subject to the terms of this Agreement, each Contributor hereby
  grants Recipient a non-exclusive, worldwide, royalty-free patent
  license under Licensed Patents to make, use, sell, offer to sell,
  import and otherwise transfer the Contribution of such Contributor,
  if any, in Source Code or other form. This patent license shall
  apply to the combination of the Contribution and the Program if, at
  the time the Contribution is added by the Contributor, such addition
  of the Contribution causes such combination to be covered by the
  Licensed Patents. The patent license shall not apply to any other
  combinations which include the Contribution. No hardware per se is
  licensed hereunder.

  c) Recipient understands that although each Contributor grants the
  licenses to its Contributions set forth herein, no assurances are
  provided by any Contributor that the Program does not infringe the
  patent or other intellectual property rights of any other entity.
  Each Contributor disclaims any liability to Recipient for claims
  brought by any other entity based on infringement of intellectual
  property rights or otherwise. As a condition to exercising the
  rights and licenses granted hereunder, each Recipient hereby
  assumes sole responsibility to secure any other intellectual
  property rights needed, if any. For example, if a third party
  patent license is required to allow Recipient to Distribute the
  Program, it is Recipient's responsibility to acquire that license
  before distributing the Program.

  d) Each Contributor represents that to its knowledge it has
  sufficient copyright rights in its Contribution, if any, to grant
  the copyright license set forth in this Agreement.

  e) Notwithstanding the terms of any Secondary License, no
  Contributor makes additional grants to any Recipient (other than
  those set forth in this Agreement) as a result of such Recipient's
  receipt of the Program under the terms of a Secondary License
  (if permitted under the terms of Section 3).

3. REQUIREMENTS

3.1 If a Contributor Distributes the Program in any form, then:

  a) the Program must also be made available as Source Code, in
  accordance with section 3.2, and the Contributor must accompany
  the Program with a statement that the Source Code for the Program
  is available under this Agreement, and informs Recipients how to
  obtain it in a reasonable manner on or through a medium customarily
  used for software exchange; and

  b) the Contributor may Distribute the Program under a license
  different than this Agreement, provided that such license:
     i) effectively disclaims on behalf of all other Contributors all
     warranties and conditions, express and implied, including
     warranties or conditions of title and non-infringement, and
     implied warranties or conditions of merchantability and fitness
     for a particular purpose;

     ii) effectively excludes on behalf of all other Contributors all
     liability for damages, including direct, indirect, special,
     incidental and consequential damages, such as lost profits;

     iii) does not attempt to limit or alter the recipients' rights
     in the Source Code under section 3.2; and

     iv) requires any subsequent distribution of the Program by any
     party to be under a license that satisfies the requirements
     of this section 3.

3.2 When the Program is Distributed as Source Code:

  a) it must be made available under this Agreement, or if the
  Program (i) is combined with other material in a separate file or
  files made available under a Secondary License, and (ii) the initial
  Contributor attached to the Source Code the notice described in
  Exhibit A of this Agreement, then the Program may be made available
  under the terms of such Secondary Licenses, and

  b) a copy of this Agreement must be included with each copy of
  the Program.

3.3 Contributors may not remove or alter any copyright, patent,
trademark, attribution notices, disclaimers of warranty, or limitations
of liability ("notices") contained within the Program from any copy of
the Program which they Distribute, provided that Contributors may add
their own appropriate notices.

4. COMMERCIAL DISTRIBUTION

Commercial distributors of software may accept certain responsibilities
with respect to end users, business partners and the like. While this
license is intended to facilitate the commercial use of the Program,
the Contributor who includes the Program in a commercial product
offering should do so in a manner which does not create potential
liability for other Contributors. Therefore, if a Contributor includes
the Program in a commercial product offering, such Contributor
("Commercial Contributor") hereby agrees to defend and indemnify every
other Contributor ("Indemnified Contributor") against any losses,
damages and costs (collectively "Losses") arising from claims, lawsuits
and other legal actions brought by a third party against the Indemnified
Contributor to the extent caused by the acts or omissions of such
Commercial Contributor in connection with its distribution of the Program
in a commercial product offering. The obligations in this section do not
apply to any claims or Losses relating to any actual or alleged
intellectual property infringement. In order to qualify, an Indemnified
Contributor must: a) promptly notify the Commercial Contributor in
writing of such claim, and b) allow the Commercial Contributor to control,
and cooperate with the Commercial Contributor in, the defense and any
related settlement negotiations. The Indemnified Contributor may
participate in any such claim at its own expense.

For example, a Contributor might include the Program in a commercial
product offering, Product X. That Contributor is then a Commercial
Contributor. If that Commercial Contributor then makes performance
claims, or offers warranties related to Product X, those performance
claims and warranties are such Commercial Contributor's responsibility
alone. Under this section, the Commercial Contributor would have to
defend claims against the other Contributors related to those performance
claims and warranties, and if a court requires any other Contributor to
pay any damages as a result, the Commercial Contributor must pay
those damages.

5. NO WARRANTY

EXCEPT AS EXPRESSLY SET FORTH IN THIS AGREEMENT, AND TO THE EXTENT
PERMITTED BY APPLICABLE LAW, THE PROGRAM IS PROVIDED ON AN "AS IS"
BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, EITHER EXPRESS OR
IMPLIED INCLUDING, WITHOUT LIMITATION, ANY WARRANTIES OR CONDITIONS OF
TITLE, NON-INFRINGEMENT, MERCHANTABILITY OR FITNESS FOR A PARTICULAR
PURPOSE. Each Recipient is solely responsible for determining the
appropriateness of using and distributing the Program and assumes all
risks associated with its exercise of rights under this Agreement,
including but not limited to the risks and costs of program errors,
compliance with applicable laws, damage to or loss of data, programs
or equipment, and unavailability or interruption of operations.

6. DISCLAIMER OF LIABILITY

EXCEPT AS EXPRESSLY SET FORTH IN THIS AGREEMENT, AND TO THE EXTENT
PERMITTED BY APPLICABLE LAW, NEITHER RECIPIENT NOR ANY CONTRIBUTORS
SHALL HAVE ANY LIABILITY FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL,
EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING WITHOUT LIMITATION LOST
PROFITS), HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
ARISING IN ANY WAY OUT OF THE USE OR DISTRIBUTION OF THE PROGRAM OR THE
EXERCISE OF ANY RIGHTS GRANTED HEREUNDER, EVEN IF ADVISED OF THE
POSSIBILITY OF SUCH DAMAGES.

7. GENERAL

If any provision of this Agreement is invalid or unenforceable under
applicable law, it shall not affect the validity or enforceability of
the remainder of the terms of this Agreement, and without further
action by the parties hereto, such provision shall be reformed to the
minimum extent necessary to make such provision valid and enforceable.

If Recipient institutes patent litigation against any entity
(including a cross-claim or counterclaim in a lawsuit) alleging that the
Program itself (excluding combinations of the Program with other software
or hardware) infringes such Recipient's patent(s), then such Recipient's
rights granted under Section 2(b) shall terminate as of the date such
litigation is filed.

All Recipient's rights under this Agreement shall terminate if it
fails to comply with any of the material terms or conditions of this
Agreement and does not cure such failure in a reasonable period of
time after becoming aware of such noncompliance. If all Recipient's
rights under this Agreement terminate, Recipient agrees to cease use
and distribution of the Program as soon as reasonably practicable.
However, Recipient's obligations under this Agreement and any licenses
granted by Recipient relating to the Program shall continue and survive.

Everyone is permitted to copy and distribute copies of this Agreement,
but in order to avoid inconsistency the Agreement is copyrighted and
may only be modified in the following manner. The Agreement Steward
reserves the right to publish new versions (including revisions) of
this Agreement from time to time. No one other than the Agreement
Steward has the right to modify this Agreement. The Eclipse Foundation
is the initial Agreement Steward. The Eclipse Foundation may assign the
responsibility to serve as the Agreement Steward to a suitable separate
entity. Each new version of the Agreement will be given a distinguishing
version number. The Program (including Contributions) may always be
Distributed subject to the version of the Agreement under which it was
received. In addition, after a new version of the Agreement is published,
Contributor may elect to Distribute the Program (including its
Contributions) under the new version.

Except as expressly stated in Sections 2(a) and 2(b) above, Recipient
receives no rights or licenses to the intellectual property of any
Contributor under this Agreement, whether expressly, by implication,
estoppel or otherwise. All rights in the Program not expressly granted
under this Agreement are reserved. Nothing in this Agreement is intended
to be enforceable by any entity that is not a Contributor or Recipient.
No third-party beneficiary rights are created under this Agreement.

Exhibit A - Form of Secondary Licenses Notice

"This Source Code may also be made available under the following 
Secondary Licenses when the conditions for such availability set forth 
in the Eclipse Public License, v. 2.0 are satisfied: {name license(s),
version(s), and exceptions or additional permissions here}."

  Simply including a copy of this Agreement, including this Exhibit A
  is not sufficient to license the Source Code under Secondary Licenses.

  If it is not possible or desirable to put the notice in a particular
  file, then You may include the notice in a location (such as a LICENSE
  file in a relevant directory) where a recipient would be likely to
  look for such a notice.

  You may add additional accurate notices of copyright ownership.
```
