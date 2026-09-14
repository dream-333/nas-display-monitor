# 固定发布输入

UI5.1/ 保留应用、引导程序、OTA 初始化数据、分区表及 CSV、来源清单、PNG 预览、图标和许可证，逐文件摘要位于 SHA256SUMS.txt。应用和分区表沿用原始发布字节；引导组件来自同一锁定工具链，来源见 BOOT-SOURCES.json。重建不自动覆盖固定输入。factory.bin 在打包时按地址合并生成，首次烧录实机验证状态见说明。

HTML/SVG 预览由 tools/render_ui_preview.py 生成到 .build/previews，不作为永久发布输入。删除预览格式时保留其余文件原始摘要。
