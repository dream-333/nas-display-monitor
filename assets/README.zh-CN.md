# 固定发布输入

UI5.1/ 仅保留固件、参考分区表、PNG 预览、图标和许可证，逐文件摘要位于 SHA256SUMS.txt。固件与分区表是已验证发布字节；重建不自动覆盖。

HTML/SVG 预览由 tools/render_ui_preview.py 生成到 .build/previews，不作为永久发布输入。删除预览格式时保留其余文件原始摘要。
