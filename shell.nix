# Build environment for Endless Sky. Provides every native dep CMake looks for
# so the build can run with -DES_USE_VCPKG=OFF (no vcpkg bootstrap on the agent).
#
# Usage:
#   nix-shell --command "cmake -B build/macos-arm -G Ninja \
#     -DES_USE_VCPKG=OFF -DCMAKE_BUILD_TYPE=Debug && cmake --build build/macos-arm"
#
{ pkgs ? import <nixpkgs> {} }:

pkgs.mkShell {
  name = "endless-sky-build";

  nativeBuildInputs = with pkgs; [
    cmake
    ninja
    pkg-config
  ] ++ pkgs.lib.optionals pkgs.stdenv.isDarwin [
    # Keep the SDK that nix's libcxx is built against in scope so the C++
    # standard headers can resolve max_align_t / errno on macOS.
    apple-sdk_26
  ];

  # On macOS, system frameworks (Cocoa, OpenGL) are provided implicitly by
  # stdenv — no need to list them. On Linux we pull in GLEW + libuuid.
  buildInputs = with pkgs; [
    SDL2
    libpng
    libjpeg
    openal
    libmad
  ] ++ pkgs.lib.optionals pkgs.stdenv.isLinux [
    libGL
    glew
    libuuid
  ];

  shellHook = ''
    echo "endless-sky build shell ready (cmake $(cmake --version | head -1 | cut -d' ' -f3))"
  '';
}
