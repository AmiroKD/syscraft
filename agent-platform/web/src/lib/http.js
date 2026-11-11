/**
 * 统一请求层：包络解包、401 上报、错误对象。
 *
 * 只做一件事——把后端 `{code,msg,data}` 摊平成 data 或抛出 HttpError。
 * 令牌放在模块级而不是每次传参：调用点（store）不该关心鉴权细节。
 */

let token = "";
let unauthorized = null;

export function setToken(value) {
  token = value || "";
}

/** 401 的唯一出口：由 auth store 注册，负责清状态回登录页。 */
export function onUnauthorized(handler) {
  unauthorized = handler;
}

/** 流式请求不经过 api()，拿到 401 时自己喊一声（见 lib/sse.js） */
export function notifyUnauthorized() {
  unauthorized?.();
}

export class HttpError extends Error {
  constructor(message, status, code) {
    super(message);
    this.name = "HttpError";
    this.status = status;
    this.code = code;
  }
}

export async function api(method, path, body) {
  let res;
  try {
    res = await fetch(path, {
      method,
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: "Bearer " + token } : {}),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (error) {
    throw new HttpError("网络不可达，请检查服务是否在运行", 0);
  }

  let data = {};
  try {
    data = await res.json();
  } catch (e) {
    /* 网关错误页不是 JSON，保持空对象 */
  }

  if (res.status === 401) {
    onUnauthorized?.();
    throw new HttpError(data.msg || "登录状态已失效，请重新登录", 401, data.code);
  }
  if (!res.ok || data.code !== 0) {
    throw new HttpError(data.msg || `请求失败 (${res.status})`, res.status, data.code);
  }
  return data.data;
}

export function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
