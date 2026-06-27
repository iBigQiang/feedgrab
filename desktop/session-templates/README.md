# feedgrab Desktop session templates

这个目录只保存空白登录态模板，供安装包在用户电脑首次安装时创建 `sessions` 目录。

- 不要把真实 Cookie、Token 或浏览器登录态提交到仓库。
- 安装后用户可以在安装目录的 `sessions` 子目录里手动填写模板。
- GUI 的导入逻辑会忽略空白模板；只有用户填写了真实值，或通过登录流程生成了有效 JSON，才会导入到运行数据目录。
