{
  "variables": {
    "zoom_sdk_dir%": ""
  },
  "targets": [
    {
      "target_name": "zoom_sdk_wrapper",
      "sources": [
        "zoom_wrapper.cpp"
      ],
      "include_dirs": [
        "<!@(node -p \"require('node-addon-api').include\")",
        "<(zoom_sdk_dir)/h",
        "<!@(pkg-config --cflags-only-I Qt5Core | sed \"s/-I//g\")"
      ],
      "libraries": [
        "-L<(zoom_sdk_dir)",
        "-lmeetingsdk",
        "<!@(pkg-config --libs Qt5Core)"
      ],
      "cflags_cc": [
        "-std=c++17",
        "-fexceptions"
      ],
      "cflags_cc!": [
        "-fno-exceptions"
      ],
      "defines": [
        "NAPI_DISABLE_CPP_EXCEPTIONS",
        "NAPI_VERSION=7"
      ]
    }
  ]
}
