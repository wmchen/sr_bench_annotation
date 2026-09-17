# 迁移来源与实现边界

依据：docs/remote/v1.md 及确认后的实施计划。
迁移时仓库基线：fe09ebc7d5a56d7f2147b1e774f723f8df86c94d。

| 新实现 | 来源和迁移内容 |
| --- | --- |
| backend/domain/rules.py | anylabeling/views/labeling/realisr_dataset.py：实际尺寸比、半偶数取整、闭区间裁剪、字段迁移、稳定 ID、默认值、LR 扩展字段继承、完成度和元信息 |
| backend/infrastructure/datasets/source.py | 同上：schema 1/2/3、OCR 真值迁移、正式四文件配对、草稿和可确定的旧 ID 匹配；独立只读扫描，不复制管理器 |
| frontend/workspace 与 state/domain.ts | realisr_workspace.py、label_widget.py：四视图、HR 编辑、LR 几何只读、形状转换起点和方向、空组确认及单调性提醒 |
| inference/vendor | services/auto_labeling/utils/ppocr_utils 的无 Qt 数值管线，保留文件内 PaddleOCR 原版权和 Apache 许可证声明 |
| vendor/ocr_defaults.py | PPOCRv4.parse_args，供其 v5/v6 继承管线使用的原始默认参数 |
| vendor/scrfd_core.py | scrfd.py 的预处理、anchor、forward、NMS、后处理纯方法，不包含 Qt 模型类和 Shape |
| inference/adapters.py | PPOCR v6 显式字典及参数覆盖顺序、选框识别 ID 映射；SCRFD RGB 预处理及倒序矩形输出 |

OCR 管线只由服务器登记的固定模型类型调用，浏览器不能提交 Python 模块或算子。
移除了与 ONNX 无关的 Paddle 环境变量写入。
迁移代码受仓库 GPLv3 和各第三方文件原有许可证约束。

## 接口区别

- SQLite 是在线唯一权威状态；JSON 用于显式导入与导出。
- 在线区域 ID 必填且唯一；旧数据仅在确定的规则内迁移。
  重复或无法匹配的 ID、不可编辑形状、非有限坐标报告错误。
  按后续用户要求，扫描导入时自动将有限越界顶点贴到闭区间图像边界；
  修正后重新同步 LR，记录 repairs，不直接改写源 JSON。贴边后退化的 HR 区域仍报错。
- LR 几何和文字由 HR 重建；HTTP 只接受 HR 与可恢复度映射。
  LR 整数取整可能使很小的区域退化，保持旧同步语义。
- 桌面可能容忍的损坏输入不是合法 HTTP 写入，公开接口严格校验。
- revision 是在线写入版本，committed_revision 指向正式快照。
  重试同一操作不递增版本；不同操作 ID 对应独立事务。
- 撤销重做作用于当前样本本次编辑；服务器新版本不能覆盖本地未保存内容。

## 依赖

HTTP 调用应用服务，领域只依赖标准库。应用服务通过存储接口协调权限、租约、
版本与事务。入口组合 SQLite、原图和推理实现。
worker 不连接数据库；模型结果统一经过应用层的事务检查。

运行入口不导入 anylabeling、PyQt、QImage、Shape 或 ModelManager。
测试可以在桌面环境对照旧实现，但不是服务端依赖。
权重、字典和配置指纹独立登记，不迁移整个桌面插件系统。

## 自动下载补充

按后续用户要求，PPOCR v6 Medium 和 SCRFD 内置来源与校验值，首次确认加载时下载。
来源沿用桌面 auto_labeling YAML 的 v3.0.0 发布地址及 ModelScope 镜像规则。
PPOCR v6 字典复制自桌面 configs/ppocr/ppocrv6_dict.txt，随独立包分发，保留同一词表。
下载器使用标准库，不引入桌面 Model 类或 Qt；显式本地 files 配置继续有效。
