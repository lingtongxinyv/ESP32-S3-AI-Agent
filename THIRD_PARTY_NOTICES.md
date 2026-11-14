# 第三方声明

本仓库包含或依赖以下第三方项目；依赖本身不由本仓库重新授权，构建时请遵循其原始许可证。

- **ESP-IDF、ESP-SR、ESP WebSocket Client、cJSON**：Espressif Systems 及其贡献者。版本与组件锁见 `firmware_idf/main/idf_component.yml`、`firmware_idf/dependencies.lock`。`srmodels.bin` 是由 ESP-SR 构建流程生成的中文识别模型分区。
- **FastAPI、SQLAlchemy、Alembic、Uvicorn、PySide6、OkHttp、Jetpack Compose、Room、Gradle/Android SDK**：分别遵循各自上游许可。
- **MicroPython SSD1306 驱动**：`单片机/ssd1306.py` 基于 micropython-lib 的 SSD1306 驱动修改，原项目为 MIT License。
- **蓝色铃铛 Logo 与启动图标**：由项目作者提供并随本项目发布。

## micropython-lib MIT License

The MIT License (MIT)

Copyright (c) 2013, 2014 micropython-lib contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.

上游：<https://github.com/micropython/micropython-lib>
