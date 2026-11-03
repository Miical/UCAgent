
# Bug 管理

项目根目录 `.flowcanvas/bugs.json` 保存唯一 Bug 列表。界面复用验证目标列表，支持搜索、展开/折叠、编辑和绑定源码查看。

AI 先调用 `bug_records(operation="guide")`，随后通过 read/create/update/inspect/bind/source/accept 管理；写操作需要完全访问和匹配的 expected_revision。记录包含说明、复现步骤、预期/实际行为、严重程度、状态、证据和关联测试用例 ID。源码绑定通过稳定标识定位；没有定位依据时不绑定。

状态为未验证、已确认、修复中、已修复。非未验证状态要求提供证据；模型须核对复现结果和修复回归，不能仅凭文字字段声称已验收。演示数据标记 example=true，只允许未验证状态，不能直接转为真实缺陷，也不计入已确认数量。
