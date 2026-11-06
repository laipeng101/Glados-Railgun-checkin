# Glados自动签到

> 基于 GitHub Actions 的 GLaDOS 自动签到工具，支持多账号与多通道推送（PushDeer / Server酱）。

本 fork 的签到只访问 `glados.cloud`。现有部署使用单账号、每日成功后跳过及独立补签。[部署说明](DEPLOYMENT.md)

## 文件结构

```shell
│  checkin.py          # 签到主脚本
│  logging_config.py   # 日志配置（北京时间、[I/W/E] 短标签）
│  requirements.txt    # Python 依赖
│
├─.github
│  └─workflows
│          gladosCheck.yml   # Actions 配置文件
```

## 食用方式

### 注册 GLaDOS 账号

注册地址：<https://glados.space/landing/0A58E-NV28S-6U3QV-33VMG>

- 我的邀请码：[`0A58E-NV28S-6U3QV-33VMG`](https://0a58e-nv28s-6u3qv-33vmg.glados.space)
- 我的优惠码（9 折）：[`DEVILSTORE`](https://0a58e-nv28s-6u3qv-33vmg.glados.space)

### Fork 本仓库

![图片加载失败](imgs/1.png)

### 配置 Secrets

跳转至自己仓库的 `Settings` → `Secrets and variables` → `Actions`，按需添加以下仓库 Secret。

#### 3.1 GLADOS_COOKIES（必填）

对应 GLaDOS 账号 Cookie 中的有效部分，获取方式：

- 在已登录的 `glados.cloud` 签到页面打开开发者工具的 `Network`，刷新页面。
- 查看发往 `glados.cloud` 的请求，在 `Request Headers` 中取得 `gld:sess`、`gld:sess.sig` 和 `User-Agent`。

  > Cookie 格式为 `gld:sess=SESSION_VALUE; gld:sess.sig=SIGNATURE_VALUE`。

- 建议用 [JSON 账号格式](DEPLOYMENT.md#optional-json-format) 将 Cookie、登录域名和同一浏览器的 User-Agent 一起保存在 `GLADOS_COOKIES` 中。现有的单行 Cookie 格式仍受支持。

![图片加载失败](imgs/2.png)
![图片加载失败](imgs/3.png)

#### 3.2 GLADOS_EXCHANGE_PLAN（非必填）

配置自动兑换积分策略：

| 值 | 积分要求 | 兑换天数 |
|---|---------|---------|
| `plan100` | 100 积分 | 10 天 |
| `plan200` | 200 积分 | 30 天 |
| `plan500` | 500 积分 | 100 天 |

> 不配置时默认不进行自动兑换；如需兑换，设置 `GLADOS_EXCHANGE_PLAN` 为 `plan100` / `plan200` / `plan500`。

#### 3.3 手机推送（非必填）

支持多个推送后端，可在 `Settings`→`Secrets and variables`→`Action` 中按需配置，**任一后端配置后即会推送，支持同时配置多个（都发）**：

- **PushDeer**：添加 `repository secret` 命名为 `PUSHDEER_SENDKEY`，其值对应 PushDeer key（[获取地址](https://www.pushdeer.com/product.html)）。
- **Server酱**：添加 `repository secret` 命名为 `SERVERCHAN_SENDKEY`，其值对应 Server酱 SendKey（[获取地址](https://sct.ftqq.com/)）。

### Star 本仓库

![图片加载失败](imgs/4.png)

## 更新日志

- **2026-11** 添加签到 challenge 支持，使用同一账号和 User-Agent 取得 nonce 后提交 proof。
- **2026-01**: 重构代码，添加日志输出方便定位，支持新版网址，支持配置积分兑换策略。
- **2026-04**: 优化代码逻辑与日志输出，支持[新版域名](https://railgun.info)，在 `GLADOS_COOKIES` 中添加新版域名下的 cookies 即可使用。
- **2026-09**: 设备平台自适应重试（无需手动配置 UA 即可自动恢复签到）；网络层自动重试与退避，降低 CI 网络抖动导致的误报失败；日志时间统一为北京时间，定时任务调整为北京时间每天 2 次并避开整点高峰；默认关闭自动兑换，需显式配置后才启用。
- **2026-10**: 日志级别短标签 `[I]/[W]/[E]` ；推送兼容 Server酱（策略模式多后端「都发」），新增 `requirements.txt` 。

## 问题排查与定位

可在 `Actions` → `GLaDOS Check-in` → `Running checkin` 查看签到结果和 challenge 状态。若服务端识别为自动签到，实际积分可能仍只有 1 分。

  <img width="1684" height="844" alt="image" src="https://github.com/user-attachments/assets/45348a5f-43e4-45f5-8fdf-ce84d343b30d" />

## 声明

本项目不保证稳定运行与更新，因 GitHub 相关规定可能会删库，请注意备份。
