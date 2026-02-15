{
  "targets": [{
    "target_name": "zoom_sdk_wrapper",
    "sources": [
      "core/src/platforms/zoom/native/src/zoom_wrapper.cpp"
    ],
    "include_dirs": [
      "<!@(node -p \"require('node-addon-api').include\")",
      "core/src/platforms/zoom/native/zoom_meeting_sdk/h",
      "<!@(pkg-config --cflags-only-I Qt5Core 2>/dev/null | tr ' ' '\\n' | sed 's/^-I//')"
    ],
    "libraries": [
    ],
    "cflags!": [ "-fno-exceptions" ],
    "cflags_cc!": [ "-fno-exceptions" ],
    "cflags_cc": [
      "-std=c++17",
      "-fexceptions"
    ],
    "defines": [
      "NAPI_DISABLE_CPP_EXCEPTIONS",
      "NAPI_VERSION=7"
    ],
    "conditions": [
      ["OS=='linux'", {
        "libraries": [
          "-L<(module_root_dir)/core/src/platforms/zoom/native/zoom_meeting_sdk",
          "-lmeetingsdk",
          "-lpthread",
          "<!@(pkg-config --libs Qt5Core 2>/dev/null)",
          "-Wl,-rpath,$$ORIGIN/../../core/src/platforms/zoom/native/zoom_meeting_sdk",
          "-Wl,-rpath,$$ORIGIN/../../core/src/platforms/zoom/native/zoom_meeting_sdk/qt_libs"
        ]
      }]
    ]
  }]
}
