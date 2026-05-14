# vince-skill

Vince 的个人技能库。

## 目录结构

```
vince-skill/
├── README.md
├── .gitignore
└── <category>/             # 按领域分类
    └── <skill-name>/       # 每个技能一个目录
        ├── SKILL.md        # 技能定义（必需）
        ├── references/     # 参考资料
        ├── templates/      # 模板文件
        ├── scripts/        # 可执行脚本
        └── assets/         # 静态资源
```

## 技能格式

每个技能是一个独立目录，核心文件是 `SKILL.md`（YAML 前置元数据 + Markdown 正文）。参考 `category-name/example-skill/` 示例。

## 许可

MIT
