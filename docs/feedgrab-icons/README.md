# feedgrab — 图标资源包

由 QM icon 生成 · 2026/6/26 17:33:13

## 目录说明
- android/ — 各密度 mipmap、自适应图标 XML（含 Android 13+ monochrome 主题层）、Play 商店 512
- ios/AppIcon.appiconset/ — 直接拖入 Xcode 的 Assets.xcassets 即可
- web/ — favicon.ico、PWA 图标、maskable、OG 社交图、manifest.json 与 HTML 片段
- macos/ — AppIcon.icns（Big Sur 风格圆角 + 留白）
- windows/ — app.ico（16~256 多尺寸）与磁贴 PNG
- custom/ — 你添加的自定义尺寸，例如 1024×1024、2048×2048
- qm-icon-config.json — 设计配置，可在 QM icon 中重新导入继续编辑

## 使用提示
- iOS 图标按规范导出为全出血方形，系统会自动应用圆角遮罩
- Android 自适应图标前景层已按 108dp 安全区缩放
- 勾选 macOS 会包含 AppIcon.icns；勾选 Windows 会包含 app.ico
- App Store 图标固定包含 1024×1024；自定义尺寸会按输入的像素边长重新渲染，不是简单拉伸小图

## 关于
- 生成工具：QM Icon Studio
- 在线地址：https://icon.qiaomu.ai/
- 品牌：向阳乔木 / 乔向阳
- 官网：https://qiaomu.ai/
- 博客：https://blog.qiaomu.ai/
- X：https://x.com/vista8
- GitHub：https://github.com/joeseesun/
