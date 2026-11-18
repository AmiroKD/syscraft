/**
 * SSE 客户端：fetch + ReadableStream 自解析（《技术栈》3.2 选型）。
 *
 * 为什么不用 EventSource：它只能发 GET、带不了 Authorization 头、也带不了请求体。
 *
 * 三条约定（对应《前端交互设计》4.3 / 4.5）：
 * - 事件里的 seq 由调用方记录，断线时用 after_seq 续传，服务端只补发缺失部分；
 * - resume=1 是「接管」而不是「发消息」，服务端不会重新派发任务；
 * - done 事件是终态，收到即结束，不再重连。
 */
import { HttpError, notifyUnauthorized, sleep } from "./http";

/**
 * 打开一条事件流。
 * @param {object} opts
 * @param {string} opts.message    用户消息（续传时为空）
 * @param {string|null} opts.sessionId
 * @param {number} opts.afterSeq   从该序号之后开始（0 = 从头）
 * @param {boolean} opts.resume    刷新后接管
 * @param {string} opts.token
 * @param {(event:object)=>void} opts.onEvent
 * @param {(text:string)=>void} [opts.onRetry] 续传提示
 * @param {AbortSignal} [opts.signal]
 */
export async function openStream(opts) {
  const { message, sessionId, resume, token, onEvent, onRetry, signal } = opts;
  let afterSeq = opts.afterSeq || 0;
  let attempt = 0;

  while (true) {
    let res;
    const url = "/api/chat/stream?after_seq=" + afterSeq + (resume ? "&resume=1" : "");
    try {
      res = await fetch(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: "Bearer " + token,
        },
        body: JSON.stringify({ message, session_id: sessionId }),
        signal,
      });
    } catch (error) {
      if (error?.name === "AbortError") return { aborted: true, lastSeq: afterSeq };
      if (attempt < 2 && afterSeq > 0) {
        attempt += 1;
        onRetry?.("连接中断，正在续传…");
        await sleep(800);
        continue;
      }
      throw new HttpError("连接中断，消息可能未送达，请重试", 0);
    }

    if (!res.ok) {
      let body = {};
      try { body = await res.json(); } catch (e) { /* 忽略 */ }
      // 流式请求绕过了 api()，401 要在这里补一次——否则页面会停在对话里只弹个错误
      if (res.status === 401) notifyUnauthorized();
      throw new HttpError(body.msg || `请求失败 (${res.status})`, res.status, body.code);
    }

    // readBody 必须包进 try：流传输中途断网时 reader.read() 会 reject，
    // 不兜住的话异常直接穿透整个续传循环
    let result;
    try {
      result = await readBody(res, (event) => {
        if (typeof event.seq === "number" && event.seq > afterSeq) afterSeq = event.seq;
        onEvent(event);
      });
    } catch (error) {
      if (error?.name === "AbortError") return { aborted: true, lastSeq: afterSeq };
      // 流中途断开：与「干净结束但没收到 done」同一处理——带序号续传，最多三次
      attempt += 1;
      if (attempt > 3) {
        throw new HttpError("连接中断且续传失败，请刷新页面查看历史", 0);
      }
      onRetry?.("连接中断，正在续传…");
      await sleep(600);
      continue;
    }

    if (result.done) return { aborted: false, lastSeq: afterSeq };
    // 连接被对端关掉但没收到 done：带序号续传，最多三次
    attempt += 1;
    if (attempt > 3) {
      throw new HttpError("连接中断且续传失败，请刷新页面查看历史", 0);
    }
    onRetry?.("连接中断，正在续传…");
    await sleep(600);
  }
}

async function readBody(res, emit) {
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let done = false;

  while (true) {
    const { done: finished, value } = await reader.read();
    if (finished) break;
    buffer += decoder.decode(value, { stream: true });

    // 事件以空行分隔；最后一段可能不完整，留在 buffer 里等下一片
    const blocks = buffer.split(/\r?\n\r?\n/);
    buffer = blocks.pop() || "";
    for (const block of blocks) {
      const event = parseBlock(block);
      if (!event) continue;
      if (event.type === "done") done = true;
      emit(event);
    }
  }

  const tail = parseBlock(buffer);
  if (tail) {
    if (tail.type === "done") done = true;
    emit(tail);
  }
  return { done };
}

function parseBlock(block) {
  const lines = block.split(/\r?\n/);
  const dataLines = [];
  for (const line of lines) {
    if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
  }
  if (!dataLines.length) return null;
  const raw = dataLines.join("\n");
  try {
    return JSON.parse(raw);
  } catch (e) {
    return null;
  }
}
