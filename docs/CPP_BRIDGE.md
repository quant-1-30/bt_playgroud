# vnxtptrader C++ 桥接机制详解

> 本文档解释 `vnxtptrader.so` 如何封装中泰官方闭源 `libxtptraderapi.so`，以及 C++ 回调如何到达 Python asyncio。

## 一、两层 C++ 类结构

vnxtptrader 是中泰官方 API 的 **Boost.Python 封装层**，不是官方 API 本身。

```
┌─────────────────────────────────────────────────────────┐
│  Python 层（你的代码）                                    │
│  trader_service.py: _TraderSpi(vnxtptrader.TraderApi)   │
│    override onQueryAsset(data, error, reqid, ...)       │
└───────────────────────▲─────────────────────────────────┘
                        │ Boost.Python pure_virtual override
                        │ get_override("onQueryAsset")(...)
┌───────────────────────┴─────────────────────────────────┐
│  vnxtptrader 层（开源 .so，你能看到的源码）                │
│  class TraderApi : public XTP::API::TraderSpi           │
│    小写方法: queryAsset()    ← Python 可调用的 API       │
│    大写回调: OnQueryAsset()  ← 被 .so 回调              │
│    processTask 线程           ← 从队列取任务调 Python    │
└───────────────────────▲─────────────────────────────────┘
                        │ this->api->QueryAsset()
┌───────────────────────┴─────────────────────────────────┐
│  官方层（闭源 libxtptraderapi.so）                        │
│  class XTP::API::TraderApi                              │
│    QueryAsset()  ← 发送网络请求                          │
│    内部网络线程   ← 收到响应后回调 spi->OnXxx             │
│  class XTP::API::TraderSpi                              │
│    虚函数: OnQueryAsset() 等（回调接口）                  │
└─────────────────────────────────────────────────────────┘
```

### 关键源码位置

| 内容 | 文件 | 行号 |
|---|---|---|
| TraderApi 类定义（继承 TraderSpi） | vnxtptrader.h | 242 |
| this->api 指针（持有官方 API） | vnxtptrader.h | 245 |
| processTask 线程创建 | vnxtptrader.h | 250-254 |
| CreateTraderApi + RegisterSpi | vnxtptrader.cpp | 4098-4099 |
| queryAsset（小写，Python 包装） | vnxtptrader.cpp | 4376 |
| OnQueryAsset（大写，官方回调） | vnxtptrader.cpp | 476 |
| processTask while 循环 | vnxtptrader.cpp | 1634 |
| processQueryAsset（struct→dict+调Python） | vnxtptrader.cpp | 2430 |
| onQueryAsset（Boost.Python override） | vnxtptrader.cpp | 2478 |

## 二、完整调用链（以 queryAsset 为例）

### 阶段 1：Python → C++ → 网络发送

```python
# trader_service.py: _do_query 协程
reqid = self._next_reqid()           # reqid = 1
q = self._register_query(reqid)      # _query_queues[1] = Queue()
ret = self._api.queryAsset(session_id, 1)   # 调 C++ 小写方法
```

```cpp
// vnxtptrader.cpp:4376
int TraderApi::queryAsset(uint64_t sessionid, int reqid) {
    return this->api->QueryAsset(sessionid, reqid);  // 调官方闭源大写方法
    // this->api 是 XTP::API::TraderApi*（官方对象）
    // QueryAsset 内部：序列化协议 → TCP 发送到中泰服务器
    // 返回 0 = 发送成功（不代表服务器已响应）
}
```

### 阶段 2：服务器响应 → 官方网络线程 → C++ 队列

官方 `.so` 内部有一个网络线程（闭源），收到 TCP 响应后：

```cpp
// 官方 .so 内部（你看不到的代码）:
//   收到 TCP 响应
//   解析协议
//   spi->OnQueryAsset(data, error, request_id, is_last, session_id)
//   ↑ spi 就是通过 RegisterSpi(this) 注册的 TraderApi 对象

// vnxtptrader.cpp:476（你能看到的回调）
void TraderApi::OnQueryAsset(XTPQueryAssetRsp *asset, XTPRI *error_info,
                             int request_id, bool is_last, uint64_t session_id) {
    Task* task = new Task();
    task->task_name = ONQUERYASSET;
    // 拷贝 C++ 结构体到堆上
    XTPQueryAssetRsp *task_data = new XTPQueryAssetRsp();
    *task_data = *asset;
    task->task_data = task_data;
    // ...
    task->task_id = request_id;     // reqid=1，用于配对
    task->task_last = is_last;
    this->task_queue.push(task);    // ← 压入 C++ 线程安全队列，立即返回
    // 不直接调 Python！避免 GIL 问题
}
```

### 阶段 3：processTask 线程 → struct 转 dict → 调 Python

```cpp
// vnxtptrader.cpp:1634
void TraderApi::processTask() {
    while (1) {
        Task* task = this->task_queue.wait_and_pop();  // 阻塞等待
        switch (task->task_name) {
            // ...
            case ONQUERYASSET:
                this->processQueryAsset(task);
                break;
        }
    }
}

// vnxtptrader.cpp:2430
void TraderApi::processQueryAsset(Task *task) {
    PyLock lock;            // ← 获取 GIL，安全调 Python
    dict data;
    if (task->task_data) {
        XTPQueryAssetRsp *d = (XTPQueryAssetRsp*)task->task_data;
        data["total_asset"] = d->total_asset;
        data["buying_power"] = d->buying_power;
        // ... 把所有 C++ 字段转成 Python dict
    }
    dict error;
    if (task->task_error) {
        error["error_id"] = ...;
        error["error_msg"] = ...;
    }
    // 调用 Python 可重写方法
    this->onQueryAsset(data, error, task->task_id, task->task_last, task->addtional_int);
    //         ↑ 小写，通过 Boost.Python 的 get_override 调用 Python override
    delete task;
}
```

```cpp
// vnxtptrader.cpp:5129 (Boost.Python wrapper)
virtual void onQueryAsset(dict data, dict error, int id, bool last, uint64_t session) {
    // 如果 Python 子类重写了 onQueryAsset，调用它
    this->get_override("onQueryAsset")(data, error, id, last, session);
}
```

### 阶段 4：Python SPI → asyncio Queue

```python
# trader_service.py: _TraderSpi（你的 Python 子类）
def onQueryAsset(self, data, error, reqid, last, session_id):
    if self._owner:
        self._owner._on_query(reqid, data, error, last, "onQueryAsset")

def _on_query(self, reqid, data, error, last, event_name):
    q = self._query_queues.get(reqid)     # reqid=1 → 找到注册的 queue
    frame = {"event": event_name, "data": ..., "error": ..., "last": last}
    self._loop.call_soon_threadsafe(q.put_nowait, frame)
    # ↑ C++ processTask 线程 → 安全投递到 asyncio 事件循环
```

### 阶段 5：asyncio 协程收到数据

```python
# trader_service.py: _do_query
frame = await asyncio.wait_for(q.get(), timeout=self._query_timeout)
# ← 阶段 4 的 put 唤醒了这个 await
yield frame
if frame["last"]:
    return    # 查询完成
```

## 三、3 线程模型

```
线程 1: asyncio 事件循环（Python 主线程）
  │  协程 _do_query: api.queryAsset(rid=1) → await q.get()
  │
  │ ──────── 时间 gap（网络往返）─────────
  │
线程 2: 官方 .so 内部网络线程（C++，闭源）
  │  收到 TCP 响应 → OnQueryAsset → task_queue.push(task) → return
  │  （不获取 GIL，不调 Python，只操作 C++ 队列）
  │
线程 3: vnxtptrader processTask 线程（C++，在 TraderApi 构造函数创建）
  │  while(1):
  │    task = task_queue.wait_and_pop()
  │    PyLock lock（获取 GIL）
  │    processQueryAsset(task):
  │      struct → dict
  │      this->onQueryAsset(dict, ...)   → get_override → Python
  │    PyLock unlock（释放 GIL）
  │
线程 1（回到）: asyncio 事件循环
    call_soon_threadsafe 的回调执行 q.put_nowait(frame)
    → await q.get() 被唤醒 → yield frame
```

### 为什么不直接从官方网络线程调 Python？

1. **GIL 死锁风险**：官方 `.so` 可能持有多把锁，如果再获取 GIL 可能死锁
2. **解耦**：网络线程只管收发，不做 Python 序列化，延迟更低
3. **串行化**：processTask 线程串行调 Python，避免多线程并发调 Python 的竞态

### 为什么 processTask 而不是直接 call_soon_threadsafe？

vnxtptrader 的 processTask 做了两件事：
1. **C++ struct → Python dict**（需要 GIL）
2. **调 Python override 方法**（需要 GIL）

我们的 Python 代码再在 override 里做 `call_soon_threadsafe`，所以实际是**双重队列**：
```
C++ task_queue → processTask → Python override → call_soon_threadsafe → asyncio Queue
```

这看起来多余，但 vnxtptrader 设计如此——它不知道你的 Python 用 asyncio，只负责把 C++ 数据变成 Python dict 后调你的 callback。asyncio 的桥接由你的 TraderService 负责。

## 四、RegisterSpi 的作用

```cpp
// vnxtptrader.cpp:4098-4099
this->api = XTP::API::TraderApi::CreateTraderApi(clientid, path, log_level);
this->api->RegisterSpi(this);
//                                  ↑
//  this 是 TraderApi 对象（继承自 TraderSpi）
//  RegisterSpi 告诉官方 API：「回调时调我的 OnXxx 方法」
```

官方 `TraderApi` 类内部维护一个 `TraderSpi*` 指针：
- `RegisterSpi(this)` 把指针设为你的 `TraderApi` 子类对象
- 网络线程收到响应时，通过这个指针调用 `spi->OnQueryAsset(...)`
- 因为 `OnQueryAsset` 是虚函数，实际调用你的 `TraderApi::OnQueryAsset` override

## 五、push 事件（如 onOrderEvent）的区别

查询类（queryAsset）和推送类（onOrderEvent）的区别：

| | 查询类 | 推送类 |
|---|---|---|
| 触发方式 | 你主动调 `api.queryAsset(rid)` | 服务器主动推送（如订单状态变化） |
| 回调签名 | `OnQueryAsset(data, error, reqid, last, session)` | `OnOrderEvent(data, error, session)` |
| reqid 配对 | 有 reqid，分发到 `_query_queues[reqid]` | 无 reqid，广播到所有 `_event_subscribers` |
| Python 处理 | `_on_query` → 按 reqid 找 queue | `_emit_event` → fan-out 到所有订阅者 |
| 结束标记 | `last=True` 表示查询完成 | 无结束，持续推送直到断开 |

## 六、`await q.get()` 异步等待机制详解

> **核心疑问**：`_do_query` 中 `api_call(reqid)` 返回后，`await q.get()` 那一刻 q 里还没数据，怎么保证能等到？

### 答案：不需要保证 `api_call` 返回时 q 里有数据

`await q.get()` 会**挂起协程**，直到 C++ 线程把数据 `put` 进去后才唤醒。这是 asyncio 异步等待的本质。

`ret == 0` 只表示"网络请求已发出"，不是"服务器已响应"。就像打电话给银行查余额，电话接通了（ret=0），但客服还没告诉你余额。

### 详细时序图

```
时间线：
─────────────────────────────────────────────────────────
t=0ms:   Python 协程: ret = api.queryAsset(session, 1)  → ret=0（发送成功）
t=0ms:   Python 协程: await q.get()  → 挂起（q 为空，让出 CPU）
t=0ms:   asyncio 事件循环: 继续处理其他协程（如 ping 请求）

t=50ms:  中泰服务器: 收到查询请求，处理，返回 TCP 响应

t=55ms:  C++ 官方网络线程: 收到 TCP → OnQueryAsset → task_queue.push(task) → return
         （不碰 Python，不获取 GIL）

t=56ms:  C++ processTask 线程:
           task = task_queue.wait_and_pop()        ← 取出 task
           PyLock lock                             ← 获取 GIL
           processQueryAsset(task):                ← C++ struct → Python dict
             data["total_asset"] = ...
             this->onQueryAsset(data, ...)         ← 调 Python override
           PyLock unlock                           ← 释放 GIL

t=57ms:  Python onQueryAsset → _on_query:
           call_soon_threadsafe(q.put_nowait, frame)
           ↑ 这一行往 q 里放了数据！并通知 asyncio "q 有数据了"

t=57ms:  asyncio 事件循环: 发现 q 有数据 → 唤醒挂起的 _do_query 协程
t=58ms:  Python 协程: frame = await q.get() 返回 → yield frame
─────────────────────────────────────────────────────────
```

### `await q.get()` 做了什么

```python
frame = await asyncio.wait_for(q.get(), timeout=15)
```

这一行执行时 **q 是空的**，但 `await` 的语义是：
1. 把当前协程**挂起**（让出 CPU 给其他协程）
2. 注册一个"q 有数据时唤醒我"的回调
3. **不阻塞线程**，asyncio 事件循环继续运行其他任务
4. 当 `q.put_nowait(frame)` 被调用时，asyncio 自动唤醒挂起的协程

如果 15 秒内没有数据到达，`wait_for` 抛出 `asyncio.TimeoutError`，yield 超时错误帧。

### `call_soon_threadsafe` 为什么必须用

`_on_query` 运行在 **C++ processTask 线程**（不是 asyncio 线程），不能直接操作 asyncio 对象：

```python
def _on_query(self, reqid, data, error, last, event_name):
    # ↓ 这行运行在 C++ processTask 线程（非 asyncio 线程）
    q = self._query_queues.get(reqid)
    frame = {"event": event_name, "data": ..., "error": ..., "last": last}

    # ↓ call_soon_threadsafe 做了两件事：
    #   1. 线程安全地把回调函数投递到 asyncio 事件循环的待执行队列
    #   2. 唤醒 asyncio 事件循环（如果它在 epoll/select 中阻塞等待）
    self._loop.call_soon_threadsafe(q.put_nowait, frame)

    # 当 asyncio 事件循环下次运行时，执行 q.put_nowait(frame)
    # put_nowait 后，asyncio 自动唤醒所有 await q.get() 的协程
```

如果直接写 `q.put_nowait(frame)`（不加 call_soon_threadsafe），虽然数据会进入 queue，但 asyncio 可能不会立即注意到，且多线程操作 asyncio Queue 不是线程安全的。

## 七、processTask → processQueryAsset → onQueryAsset 完整调用链

```
processTask (while 循环，阻塞等待 task_queue)
  │
  ├─ task = task_queue.wait_and_pop()      ← 从队列取出 task
  │
  ├─ switch(task->task_name)               ← 根据 task 类型分发
  │    case ONQUERYASSET:
  │      → processQueryAsset(task)         ← vnxtptrader.cpp:2430
  │
  └─ processQueryAsset(task):
       │
       ├─ PyLock lock;                     ← 获取 GIL
       │
       ├─ dict data;                       ← 创建 Python dict
       │  data["total_asset"] = ...;       ← C++ struct 字段 → Python dict
       │  data["buying_power"] = ...;
       │  ...
       │
       ├─ dict error;
       │  error["error_id"] = ...;
       │  error["error_msg"] = ...;
       │
       └─ this->onQueryAsset(data, error, task->task_id, task->task_last, task->addtional_int);
            │
            │  ← 这是 Boost.Python 的 virtual 函数
            │
            └─ vnxtptrader.cpp:5129:
                 this->get_override("onQueryAsset")(data, error, id, last, session);
                   │
                   │  ← 调用你在 Python 中 override 的方法
                   │
                   └─ _TraderSpi.onQueryAsset(data, error, reqid, last, session_id)
                        │
                        └─ self._owner._on_query(reqid, data, error, last, "onQueryAsset")
                             │
                             └─ call_soon_threadsafe(q.put_nowait, frame)
                                  │
                                  └─ asyncio 事件循环执行 put → 唤醒 await q.get()
```

### `processQueryAsset` vs `onQueryAsset` 的区别

这是两个**不同的函数**，容易混淆：

| 函数 | 大小写 | 位置 | 职责 |
|---|---|---|---|
| `processQueryAsset` | 驼峰 | vnxtptrader.cpp:2430 | **数据转换**：C++ struct → Python dict，然后调 onQueryAsset |
| `OnQueryAsset` | 首字母大写 | vnxtptrader.cpp:476 | **C++ 回调入口**：被官方 .so 调用，push 到 task_queue |
| `onQueryAsset` | 首字母小写 | vnxtptrader.cpp:2478 → 5129 | **Python 桥接**：通过 Boost.Python get_override 调 Python override |
| `onQueryAsset` (Python) | 全小写 | trader_service.py:70 | **你的代码**：_TraderSpi 重写的方法 |

调用顺序：`OnQueryAsset`(push task) → `processTask`(pop task) → `processQueryAsset`(转换) → `onQueryAsset`(调 Python)
