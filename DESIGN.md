---
name: Ministry Policy Downloader
description: 面向本地政策归档的清晰、可信操作台
colors:
  primary: "#14532D"
  primary-hover: "#166534"
  masthead: "#102419"
  canvas: "#F2F0E8"
  surface: "#FFFEF8"
  ink: "#162019"
  muted: "#59645B"
  divider: "#CBD2C5"
  success: "#166534"
  warning: "#92400E"
  danger: "#991B1B"
  terminal: "#112018"
typography:
  display:
    fontFamily: "ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, Microsoft YaHei UI, sans-serif"
    fontSize: "clamp(2rem, 4vw, 4.25rem)"
    fontWeight: 750
    lineHeight: 1.03
    letterSpacing: "-0.03em"
  body:
    fontFamily: "ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, Microsoft YaHei UI, sans-serif"
    fontSize: "1rem"
    fontWeight: 400
    lineHeight: 1.6
  data:
    fontFamily: "ui-monospace, SFMono-Regular, Consolas, Liberation Mono, monospace"
    fontSize: "0.875rem"
    fontWeight: 500
    lineHeight: 1.55
rounded:
  control: "8px"
  surface: "14px"
spacing:
  tight: "8px"
  control: "14px"
  section: "32px"
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.surface}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "12px 18px"
  button-primary-hover:
    backgroundColor: "{colors.primary-hover}"
    textColor: "{colors.surface}"
  input:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "11px 12px"
---

## Overview

**Creative North Star: “公开档案登记台”**

界面借用档案登记、检索目录与工作票的秩序感，但保持现代浏览器工具的直接操作。首屏必须让用户在几秒内理解：选择来源与日期、预览范围、开始下载、查看缺口。表达来自真实状态和归档结构，不用装饰性仪表盘制造忙碌感。

**The One-Desk Rule.** 四个部委属于同一连续工作面，状态可以横向比较，不拆成四个孤立产品卡片。

## Colors

深森林绿标识可信的本地归档动作；温和纸张底色适合长时间阅读；正文工作面接近白色。成功、警告与失败必须同时配文字。

## Typography

中文与拉丁字母统一使用系统无衬线，降低安装负担并保持跨平台原生感。路径、URL、计数和时间戳使用等宽字体；普通标题不用等宽字体扮演技术感。

## Layout

宽屏采用左侧配置、右侧运行记录的非对称工作台；窄屏按“范围 → 操作 → 结果”自然堆叠。主要动作在无需滚动的位置，正文行宽不超过 72ch。

**The Visible State Rule.** 当前来源、日期、输出目录、运行阶段和失败恢复方法始终在同一屏可找到。

## Elevation & Depth

依靠纸面色阶、一像素分隔和单一柔和阴影建立层级。日志区为深色反转表面，明确区分机器输出与普通说明。

## Shapes

控件采用紧凑圆角，主要工作面保持较大但克制的圆角。状态标签可以用胶囊形，内容容器不得堆叠成圆角卡片墙。

## Components

- 主要按钮只承担“开始下载”，运行时进入明确加载与禁用状态。
- 预览是次要动作，停止是危险动作；三者不可共享同等视觉权重。
- 来源选择以可扫描的连续清单呈现，包含全称、简称和启用状态。
- 错误要说明问题、受影响来源与重试方式；空状态提示下一步而不是只写“暂无数据”。

## Do's and Don'ts

- **Do** 把索引覆盖、成功下载和缺口分别显示。
- **Do** 在桌面和手机宽度保留键盘焦点与可读日志。
- **Do** 使用真实运行状态和示例数据，并把示例明确标记。
- **Don't** 宣称官网历史绝对完整或政府官方背书。
- **Don't** 使用渐变文字、玻璃拟态、装饰性网格和一组同尺寸指标卡。
- **Don't** 仅靠绿色或红色表达结果。
