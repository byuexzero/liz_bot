# 三套 SDGB / maimai DX 实现逻辑对比

对比对象：

| 代号            | 位置                                  | 语言             | 规模                |
| ------------- | ----------------------------------- | -------------- | ----------------- |
| **sdgb**      | `sdgb/`                             | Python 3       | ~1400 行（含爬虫/数据更新） |
| **eaquira**   | `eaquira/sdgb/`                     | Python 3       | ~900 行            |
| **Lionheart** | `Lionheart-main.zip` → `lionheart/` | **TypeScript** | ~3070 行           |

> 分析时已将 zip 解包到 `_tools/_cmp/lionheart/`（临时工作区，可随时删除）。

---

## 一、一句话结论

|               | 定位              | 成熟度 | 本质                                            |
| ------------- | --------------- | --- | --------------------------------------------- |
| **sdgb**      | 一次性脚本集          | 低   | 硬编码参数的「能跑就行」工具                                |
| **eaquira**   | 流程化脚本（1.53 重构中） | 中   | 把 sdgb 的散装代码整理成 `MaimaiClient` 类 + payload 模块 |
| **Lionheart** | **可发布的 npm 库**  | 高   | 完整 SDK：类型系统、自动握手、多区服、缓存、错误重试                  |

三者是**同一协议的三代演化**，但 Lionheart 是重写而非移植。

---

## 二、加密层：协议一致，工程实现不同

这是三者最核心也最一致的部分。

### 2.1 maimai 业务接口加密（AES-128-CBC + zlib + MD5 混淆）

三方逻辑完全等价：

```
明文 JSON → zlib 压缩 → AES-CBC/PKCS7 加密 → POST
响应 → AES-CBC 解密 → zlib 解压 → JSON
```

| 环节  | sdgb                           | eaquira             | Lionheart                            |
| --- | ------------------------------ | ------------------- | ------------------------------------ |
| 压缩  | `zlib.compress`                | `zlib.compress`     | `pako.deflate`                       |
| 加密  | `Crypto.Cipher.AES`            | `Crypto.Cipher.AES` | `node-forge` AES-CBC                 |
| 混淆名 | `md5(api + "MaimaiChn" + Obf)` | 同左                  | `md5(api + "Maimai" + region + Obf)` |
| 大整数 | 无（Python int 天然支持）             | 无                   | `json-bigint` + `useNativeBigInt`    |

**混淆算法三方完全一致**——这是互通的根基：

```python
# sdgb / eaquira
def get_hash_api(api):
    return md5((api + "MaimaiChn" + ObfuscateParam).encode()).hexdigest()
```

```ts
// Lionheart：region 拼在中间
private _Obfuscator(apiName: string) {
  return md5(apiName + this.encryption.maimai.obfuscateParam, ...)
}
// 调用时 realName = `${apiName}Maimai${this.region}`  → apiName + "MaimaiChn"
```

### 2.2 Aime 接口加密（基板握手）

差异最大的一环。

|    | sdgb / eaquira       | Lionheart                      |
| -- | -------------------- | ------------------------------ |
| IV | **固定全零**             | **随机 16 字节**                   |
| 构造 | `E_0(0¹⁶ ‖ content)` | `randomIV ‖ E_randIV(content)` |
| 密钥 | 代码内硬编码字节数组           | 调用方通过 `CreateOption` 传入        |

**两者解密时等价**：eaquira 用 `resp[:16]` 当 IV 并解密整个响应再丢弃前 16 字节；Lionheart 只解密 `data.slice(16)`。由于 CBC 的分组特性 `P_i = D(C_i) ⊕ C_{i-1}`，多解一个分组后丢弃，结果完全相同——只是 eaquira 多算了一个分组。

⚠️ 但 eaquira 用**固定零 IV** 在密码学上是缺陷（相同明文产生相同密文），Lionheart 的随机 IV 才是正确做法。

### 2.3 密钥版本（三方各锁一个版本）

| 实现                 | `Mai-Encoding`                  | 密钥组定义位置                          |
| ------------------ | ------------------------------- | -------------------------------- |
| sdgb / sdgb        | `1.50`                          | `sdgb/sdgb.py:15-19`             |
| sdgb / getuserdata | `1.50`                          | `sdgb/getuserdata/sdgb.py:15-19` |
| eaquira            | `1.53`                          | `eaquira/sdgb/encrypt.py:9-22`   |
| Lionheart          | **参数化**（`CreateOption.version`） | 由调用方传入                           |

eaquira 的 `encrypt.py` 把三代密钥都留了注释（1.40 / 1.52 / 1.53），是三者中唯一有版本档案意识的。Lionheart 完全不内置密钥——设计上更干净，但使用门槛更高。

---

## 三、传输层：差距最大的地方

| 能力     | sdgb              | eaquira           | Lionheart                             |
| ------ | ----------------- | ----------------- | ------------------------------------- |
| HTTP 库 | `httpx` 同步        | `httpx` 异步        | `node:http(s)` 原生 + fetch 兼容层         |
| 端点地址   | **硬编码** wahlap 域名 | **硬编码** wahlap 域名 | **动态发现**（`/net/initialize` 返回 `uri1`） |
| 区服     | 硬编码 `Chn`         | 硬编码 `Chn`         | **`Chn` / `Exp` 自动判定**（`country` 字段）  |
| 重试     | ❌ 无               | ❌ 无（异常返回 `None`）  | ✅ 循环重试至 200，`maxTimeout` 秒            |
| Cookie | ❌ 不处理             | ❌ 不处理             | ✅ 按 userId 维护 `Map<number,string>`    |
| 超时     | ❌ 未设置             | 10s               | 可配                                    |
| TLS 校验 | 默认                | `verify=False`    | `rejectUnauthorized` 可配               |
| 代理     | 见 §6.2 隐患         | ❌                 | ✅ HTTP/SOCKS5 均支持                     |
| 用户代理   | `hash#userId`     | `hash#userId`     | `hash#userId` 或 `hash#shortChipId`    |

**架构性差异**：Lionheart 先做一次 allnet `/net/initialize` 握手，从响应中拿到机厅信息（`place_id`、`region0`、`country`）和真实业务端点，之后才发业务请求。两个 Python 实现则把机厅信息写在配置里、端点写死在代码里——换个服务器或换区服就得改代码。

---

## 四、业务层：能力覆盖

### 4.1 API 覆盖

|           | 覆盖方式                                              | 数量           |
| --------- | ------------------------------------------------- | ------------ |
| sdgb      | 通用 `sdgb_api(data, useApi, userId)`，API 名靠调用方传字符串 | 无约束          |
| eaquira   | 同上，但有 `MaimaiClient.call_api` 封装                  | 约 12 个（散落调用） |
| Lionheart | **26 个 API 全量类型定义**（`src/typings/api/`）           | 26           |

Lionheart 的 26 个 API 覆盖了 Python 版完全没有的领域：`GetUserCardApi`、`GetUserCharacterApi`、`GetUserCourseApi`、`GetUserFavoriteApi`、`GetUserGhostApi`、`GetUserItemApi`、`GetUserLoginBonusApi`、`GetUserMapApi`、`GetUserMusicApi`、`GetUserRegionApi`、`GetUserRecommendRateMusicApi` 等。

### 4.2 上号流程

**eaquira**（`ticket.py`）——完整流程，7 步串行：

```
Preview 探测(是否已登录) → UserLogin → 并发拉取 7 个 GetUser* → sleep(60) 模拟游戏时长
  → UploadUserPlaylogList → UpsertUserAll → UserLogout
```

注意 `time.sleep(60)` 是**同步阻塞**，写在 async 函数里会卡死整个事件循环——应改 `asyncio.sleep(60)`。

**Lionheart** —— 面向对象状态机：

```
Client.create() 握手 → client.login() 返回 User 实例
  → user.score / user.data / user.rating … 懒加载 + 缓存
  → user.logout() / user.relogin()
```

Lionheart 把「已登录」当成 `User` 对象的状态来管理（`loginId` / `loginDateTime` / `isLogin`），而 Python 版是一次性脚本、无状态。

### 4.3 数据模型

Lionheart 有独立的容器层（`src/containers/`）：

- `Score.ts` —— 成绩集合，`convertAchievementToScoreRank()` 按 13 档边界（D→SSS）换算评级，&#x542B;*&#x20;*&#x55;tage 双倍边界和 SSS+ 特判
- `Character.ts` / `Item.ts` / `Mission.ts` —— 角色、道具、任务

Python 版**完全没有这一层**——都是裸 `dict`，业务逻辑靠手写字典键。

### 4.4 独有能力

Lionheart 独有：

- **`getOpt()`** —— 完整的游戏镜像下发协议（`/net/delivery/instruction`，解析 `INSTALL\d+=` 提取 `.app`/`.opt` URL）。Python 版没有。
- **`parseQR()`** —— 结构化解析二维码
- **类型系统** —— `UserOption` 就有 7.7 KB 的类型定义

Python 版独有：

- **`sdgb/getuserdata/`** —— 批量爬虫 + SQLite 落库（`crawler.py`、`updatedata.py` 427 行），这是**数据采集**方向，Lionheart 作为客户端库不做这件事
- **`sdgb/decrypt.py`** —— 本地 MITM 代理，抓包解密调试用

---

## 五、二维码（QR / AIME）解析：一个实质分歧

|     | sdgb / eaquira   | Lionheart              |
| --- | ---------------- | ---------------------- |
| 时间戳 | **本地生成**（当前东京时间） | **从二维码内提取**（第 8–20 字符） |
| 载荷  | 取**后 64 字符**     | 取第 20 字符之后             |
| 实现  | `qr_code[-64:]`  | `qrCode.slice(20)`     |

二维码结构为 `SGWCMAID` (8) + `YYMMDDHHMMSS` (12) + payload (64)。

- Lionheart 用二维码**自带的**时间戳 → 签名与二维码严格对应
- Python 版丢弃二维码时间戳、用当前时间重新生成

**后果**：二维码若已生成一段时间，Python 版的时间戳与二维码不匹配，服务端可能返回过期错误（`errorID != 0`）。Python 版把 `errorID` 交给调用方判断，Lionheart 则直接抛异常。

两者密钥派生一致：`SHA256(chipID + timestamp + <固定盐>).upper()`。

---

## 六、交叉验证发现的缺陷

### 6.1 🔴 eaquira 存在真实运行时 Bug（缺失函数）

`eaquira/sdgb/ticket.py:58` 调用：

```python
requestData_UserPlaylog = UserPlaylog_payload(loginId, musicData, GeneralUserInfo[0])
```

但**当前 `eaquira/sdgb/payload.py` 中已无此函数**（只有 `UserAll_payload`）。定义仅存在于备份：

```
_backup/_review/eaquira-master/sdgb/payload.py:48:def UserPlaylog_payload(...)
```

→ `eaquira/sdgb/ticket.py` 一跑就 `NameError`。这是 1.50 → 1.53 重构时**漏搬**的函数。

> 注：早前判断「该函数已并入新版 payload.py」是**错误**的，实际未并入。此处更正。

### 6.2 🟠 sdgb 硬编码代理

`sdgb/getuserdata/sdgb.py:78`：

```python
client = httpx.Client(proxy="socks5://<第三方代理IP>:1100")
```

同文件的 `sdgb/sdgb.py` 无此行。疑为调试残留——所有请求都被劫持到该代理，代理一旦失效整个数据更新流程就挂。且这是明文第三方 IP（原文档里写的是真实地址，此处已隐去）。

### 6.3 🟠 敏感信息硬编码

| 位置                                | 内容                  |
| --------------------------------- | ------------------- |
| `eaquira/sdgb/settings.py`        | 真实 userId + 完整扫卡二维码 |
| `eaquira/sdgb/chime.py:35`        | 完整二维码（`__main__` 里） |
| `sdgb/call_api.py:80`             | 完整二维码               |
| `sdgb/sdgb.py:20` / `getuserdata` | KeychipID           |
| 各 `encrypt.py` / `sdgb.py`        | AES 密钥明文            |

`eaquira/sdgb/settings.py` 顶部自己写了 `# DO NOT share your env to others.`，但二维码是**可直接上号的凭据**，泄露等于账号被接管。

### 6.4 🟡 sdgb 内部大量重复

- `sdgb/sdgb.py` 与 `sdgb/getuserdata/sdgb.py` 近乎逐字重复（仅差代理行和日志级别）
- `sdgb/神秘字符串加解密脚本.py` 又抄了一遍同样的 `aes_pkcs7` 类

同一份加密代码在本项目存在 **4 份副本**。建议抽公共模块（Lionheart 的 `src/encryption/` 就是这个思路）。

### 6.5 🟡 eaquira 的 async 误用

`eaquira/sdgb/ticket.py:54` 在 `async def` 内使用 `time.sleep(60)`，阻塞事件循环。应改 `await asyncio.sleep(60)`。

---

## 七、架构对照图

```
sdgb/                    eaquira/sdgb/              lionheart/ (TypeScript)
─────────────            ──────────────             ────────────────────────
sdgb.py                  sdgb.py                    client.ts
  sdgb_api()               MaimaiClient               Client.request()  ← 重试/Cookie
  qr_api()                   .call_api()              Client.create()   ← 握手发现端点
  aes_pkcs7                encrypt.py                 Client.login()
                            aes_pkcs7                 Client.parseQR()
getuserdata/               chime.py                   Client.getOpt()
  crawler.py  ←爬虫          qr_api()                 
  updatedata.py            payload.py                 user.ts (1648 行)
  sdgb.py (副本)             UserAll_payload()          ├ 懒加载 getter + 缓存
                           ticket.py                    ├ score/data/rating/…
  call_api.py ← 8位校验      run_workflow()  ←流程       └ upsert / logout
  decrypt.py ← MITM 代理    settings.py ←配置           containers/
                           keychip.py ← 握手             ├ Score.ts  ← 评级换算
                                                         ├ Character/Item/Mission
                                                       encryption/
                                                         ├ maimai.ts  ← AES-CBC
                                                         └ aime.ts    ← 随机 IV
                                                       typings/api/  ← 26 API 类型
                                                       utils/
                                                         ├ http.ts    ← 代理/原生 http
                                                         └ date/event/iterator
```

---

## 八、结论

1. **协议层三者互通**——AES-CBC + zlib + MD5 混淆的算法完全一致，只是版本密钥不同（sdgb=1.50，eaquira=1.53，Lionheart 参数化）。Lionheart 生成的混淆名在 `Chn` 区与 Python 版**逐字节相同**。
2. **工程成熟度差一个代际**。Python 版是「硬编码端点 + 一次性脚本」；Lionheart 是「动态握手 + 有状态对象 + 类型系统 + 缓存 + 重试」的完整 SDK。若要长期维护，Lionheart 的设计明显更值得参考——**尤其是动态端点发现和多区服支持**，这两点让它在服务器变更时无需改代码。
3. **但 Python 版有 Lionheart 没有的东西**：批量数据采集（`getuserdata/`）和本地抓包调试（`decrypt.py`）。这两者是工具链而非客户端库，定位不同，不该被 Lionheart 取代。
4. **最该修的是 §6.1 的缺失函数**——这是会让 eaquira 直接跑不起来的硬伤。其次 §6.2 的硬编码代理和 §6.4 的四份加密副本。

### 建议动作（按优先级）

| 优先级  | 动作                                                                                                          |
| ---- | ----------------------------------------------------------------------------------------------------------- |
| 🔴 高 | 从 `_backup/_review/eaquira-master/sdgb/payload.py:48` 取回 `UserPlaylog_payload` 补进 `eaquira/sdgb/payload.py` |
| 🔴 高 | 移除 `sdgb/getuserdata/sdgb.py:78` 的硬编码代理，改为可选配置                                                              |
| 🟠 中 | 轮换 `eaquira/sdgb/settings.py` 等处的二维码凭据（已多次落入明文文件）                                                           |
| 🟠 中 | 抽取公共加密模块，消除 4 份副本                                                                                           |
| 🟡 低 | eaquira 的 `time.sleep(60)` → `await asyncio.sleep(60)`                                                      |
| 🟡 低 | 参考 Lionheart 改为动态端点发现，摆脱硬编码域名                                                                               |

---

*分析基于 2026-09-22 快照。敏感值（密钥、二维码、userId、代理 IP 除外）在本文档中已用版本标签代替，未复制实际值。*
