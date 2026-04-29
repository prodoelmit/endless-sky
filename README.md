# Endless Sky — TeamCity / Dolt demo fork

This fork demonstrates how **TeamCity** can use **Dolt** as the source of truth
for build-time game data. The upstream Endless Sky source is unchanged; the
twist is in the build pipeline:

- **Build A — `Export Dolt Data`.** Connects to a running `dolt sql-server`
  hosting [dolthub/endless-sky](https://www.dolthub.com/repositories/dolthub/endless-sky),
  runs `scripts/export-dolt-to-data.py`, and emits Endless Sky `.txt` data
  files (`color.txt`, `galaxies.txt`, `outfits.txt`, `outfitters.txt`,
  `stars.txt`). These files are published as a TeamCity build artifact.
- **Build B — `Endless Sky`.** Has a snapshot + artifact dependency on Build A.
  It downloads the artifact into `data/from-dolt/`, then runs the standard
  CMake build. The resulting binary needs no Dolt at runtime — it is a vanilla
  Endless Sky build whose data has been "baked" by the upstream pipeline.

A change in Dolt → trigger Build A → trigger Build B → new release artifact.
That is the demo loop.

## TeamCity setup

### Build A — `Export Dolt Data`

- **VCS root:** this repo.
- **Agent prerequisites:**
    - `dolt` in `PATH` (any recent build).
    - `python3` ≥ 3.9 with `mysql-connector-python`
      (`python3 -m pip install mysql-connector-python`).
- **Build steps:**
    1. *Start a Dolt sql-server* (one option: a docker service container, or
       a `dolt clone dolthub/endless-sky datadb && cd datadb && dolt sql-server -H127.0.0.1 -udolt &`
       in a script step, then poll `nc -z 127.0.0.1 3306`). The script assumes
       the server is already reachable.
    2. *Run the exporter*:
       ```
       python3 scripts/export-dolt-to-data.py \
         --host 127.0.0.1 --port 3306 \
         --user dolt --schema datadb \
         --out artifacts/data-from-dolt/
       ```
- **Artifact rule:** `artifacts/data-from-dolt => data-from-dolt.zip`
- **Triggers:** schedule (e.g. nightly), manual, or — if you wire up a Dolt
  VCS root via a TeamCity plugin — on every Dolt commit.

### Build B — `Endless Sky`

- **VCS root:** this repo.
- **Dependencies on Build A:**
    - **Snapshot dependency** on the latest successful Build A.
    - **Artifact dependency** with rule
      `data-from-dolt.zip!** => data/from-dolt/`
      so the exported `.txt` files land in `data/from-dolt/` before configure.
- **Build steps:** vanilla CMake — e.g. `cmake . --preset linux-ci` then
  `cmake --build . --preset linux-ci`. See [CMake build instructions](docs/readme-cmake.md).
- **Artifact rule:** pack the binary plus `data/`, `images/`, and `sounds/`
  as the release artifact.

The key demo point: **Build B has no Dolt installed.** All Dolt knowledge lives
in Build A; Build B only sees `.txt` files in a directory it would have read
anyway. The data pipeline is fully decoupled from the application pipeline,
which is what TeamCity artifact dependencies are designed to enable.

## Running the exporter locally

```bash
# 1. Get the data and start Dolt's MySQL-protocol server.
dolt clone dolthub/endless-sky datadb
(cd datadb && dolt sql-server -H127.0.0.1 -P3307 &)

# 2. Export to the directory the game already reads.
#    (any user that exists works; modern Dolt creates root@localhost on first run)
python3 -m pip install mysql-connector-python  # one-time
python3 scripts/export-dolt-to-data.py \
  --host 127.0.0.1 --port 3307 --user root --schema datadb \
  --out data/from-dolt/

# 3. Build and run as usual — no Dolt server needed at runtime.
nix-shell --run 'cmake -B build/macos-arm -G Ninja \
  -DES_USE_VCPKG=OFF -DCMAKE_BUILD_TYPE=Debug \
  -DCMAKE_OSX_ARCHITECTURES=arm64 \
  -DCMAKE_CXX_COMPILER=/usr/bin/clang++ \
  -DCMAKE_OSX_SYSROOT=$(xcrun --show-sdk-path) \
  -DUUID_INCLUDE=$(xcrun --show-sdk-path)/usr/include \
  -DCMAKE_COMPILE_WARNING_AS_ERROR=OFF'
cmake --build build/macos-arm -j

./build/macos-arm/endless-sky --resources . --outfits  # smoke-test data load
```

`shell.nix` provides `cmake`, `ninja`, SDL2, libpng, libjpeg, OpenAL, and
libmad. The exported `.txt` files are gitignored (see `.gitignore`); only
`data/from-dolt/.gitkeep` is tracked.

> **macOS arm64 + Xcode 26 note.** Upstream Endless Sky's `source/Audio.cpp`
> declares a local `queue` map that collides with `std::queue` once libc++ in
> macOS SDK 26 transitively exposes it via `<map>`. Build B's TeamCity step
> on Linux is unaffected; on a current Apple Silicon agent rename the local
> to `soundQueue` (this fork applies that fix).

------

# Endless Sky

Explore other star systems. Earn money by trading, carrying passengers, or completing missions. Use your earnings to buy a better ship or to upgrade the weapons and engines on your current one. Blow up pirates. Take sides in a civil war. Or leave human space behind and hope to find some friendly aliens whose culture is more civilized than your own...

------

Endless Sky is a sandbox-style space exploration game similar to Elite, Escape Velocity, or Star Control. You start out as the captain of a tiny spaceship and can choose what to do from there. The game includes a major plot line and many minor missions, but you can choose whether you want to play through the plot or strike out on your own as a merchant or bounty hunter or explorer.

See the [player's manual](https://github.com/endless-sky/endless-sky/wiki/PlayersManual) for more information, or the [home page](https://endless-sky.github.io/) for screenshots and the occasional blog post.

## Installing the game

Official releases of Endless Sky are available as direct downloads from [GitHub](https://github.com/endless-sky/endless-sky/releases/latest), on [Steam](https://store.steampowered.com/app/404410/Endless_Sky/), and on [Flathub](https://flathub.org/apps/details/io.github.endless_sky.endless_sky). Other package managers may also include the game, though the specific version provided may not be up-to-date.

## System Requirements

Endless Sky has very minimal system requirements, meaning most systems should be able to run the game. The most restrictive requirement is likely that your device must support at least OpenGL 3.

|| Minimum | Recommended |
|---|----:|----:|
|RAM | 750 MB | 2 GB |
|Graphics | OpenGL 3.0 | OpenGL 3.3 |
|Storage Free | 350 MB | 1.5 GB |

## Building from source

Most development is done on Linux and Windows, using CMake ([build instructions](docs/readme-cmake.md)) to compile the project. For those wishing to use an IDE, project files are provided for [Code::Blocks](https://www.codeblocks.org/) to simplify the project setup, and other IDEs are supported through their respective CMake integration. [SCons](https://scons.org/) was the primary build tool up until 0.9.16, and some files and information continue to be available for it.
For full installation instructions, consult the [Build Instructions](docs/readme-developer.md) readme.

## Contributing

As a free and open source game, Endless Sky is the product of many people's work. Contributions of artwork, storylines, and other writing are most in-demand, though there is a loosely defined [roadmap](https://github.com/endless-sky/endless-sky/wiki/DevelopmentRoadmap). Those who wish to [contribute](docs/CONTRIBUTING.md) are encouraged to review the [wiki](https://github.com/endless-sky/endless-sky/wiki), and to post in the [community-run Discord](https://discord.gg/ZeuASSx) beforehand. Those who prefer to use Steam can use its [discussion rooms](https://steamcommunity.com/app/404410/discussions/) as well, or GitHub's [discussion zone](https://github.com/endless-sky/endless-sky/discussions).

Endless Sky's main discussion and development area was once [Google Groups](https://groups.google.com/g/endless-sky), but due to factors outside our control, it is now inaccessible to new users, and should not be used anymore.

## Licensing

Endless Sky is a free, open source game. The [source code](https://github.com/endless-sky/endless-sky/) is available under the GPL v3 license, and all the artwork is either public domain or released under a variety of Creative Commons (and similarly permissive) licenses. (To determine the copyright status of any of the artwork, consult the [copyright file](https://github.com/endless-sky/endless-sky/blob/master/copyright).)
