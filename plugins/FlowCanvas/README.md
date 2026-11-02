
# FlowCanvas

UCAgent 的可视化验证工作台插件。当前提供布局骨架，无验证任务或模型调用。

左侧图标栏依次提供验证进度、验证目标、测试用例和 Bug 管理入口；展开栏顶部显示 UCAgent FlowCanvas 品牌名与当前视图标题，中间为对话栏，右侧为详情/运行栏。两处分隔条可拖动，双击恢复默认比例，宽度保存在浏览器中。对话控件暂未接入。

默认使用 Light 主题，左下角设置入口可切换与 Master 共用的 Dark / Light / Graphite 配色，并记住用户选择。

## 从源码启动

在 UCAgent 仓库根目录，使用已安装 UCAgent 依赖的 Python：

```bash
PYTHONPATH=plugins/FlowCanvas/src:. python -m flowcanvas --port 8810
```

打开 http://127.0.0.1:8810/ 。服务仅绑定本机。插件通过 `ucagent.plugins` 入口声明，前端和 Web 服务均由插件自身维护；不修改 UCAgent 核心或 Master 页面。
