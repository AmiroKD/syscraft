<script setup>
/**
 * 管理员面板：账号 / 角色 / 表级授权。
 *
 * 权限操作的守门人是「带角色校验的专用界面」，刻意不在对话里开放：
 * - 初始密码只在这里一次性展示，不进任何对话历史；
 * - 平台侧只做入口角色判断，能力门槛（MANAGE_USER、层级不可越权）与
 *   操作审计都在产物侧——这里看到的 403 是产物判定的，前端不重复实现权限。
 */
import { computed, reactive, ref, watch } from "vue";
import AppIcon from "./AppIcon.vue";
import BaseDialog from "./BaseDialog.vue";
import { api } from "../lib/http";
import { useAuthStore } from "../stores/auth";
import { useUiStore } from "../stores/ui";

const auth = useAuthStore();
const ui = useUiStore();

const open = computed(() => ui.dialog === "admin");
const tab = ref("users");
const loading = ref(false);

const users = ref([]);
const usersTotal = ref(0);
const roles = ref([]);
const grants = ref([]);

const keyword = ref("");
const roleFilter = ref("");
const enabledFilter = ref("");
const page = ref(1);
const SIZE = 20;

const showCreate = ref(false);
const creating = ref(false);
const createForm = reactive({ username: "", password: "", displayName: "", roleCode: "" });

const grantForm = reactive({ subjectType: "ROLE", subjectId: null, entity: "", access: "READ", expiresAt: "" });
const showGrantForm = ref(false);
const granting = ref(false);

const pwResult = ref(null); // { username, initialPassword } 一次性展示
const copied = ref(false);

watch(open, (value) => {
  if (value) {
    tab.value = "users";
    pwResult.value = null;
    resetCreateForm();
    showGrantForm.value = false;
    refresh();
  }
});
watch(tab, (value) => {
  if (value === "grants") loadGrants();
  if (value === "users") refresh();
});

function resetCreateForm() {
  createForm.username = "";
  createForm.password = "";
  createForm.displayName = "";
  createForm.roleCode = "";
  showCreate.value = false;
}

function fmtDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function roleOf(code) {
  return roles.value.find((r) => r.code === code) || null;
}
function roleLabel(code) {
  return roleOf(code)?.name || code || "—";
}

/** 可分配的角色：层级必须低于自己（产物侧会拒绝同级或更高级，前端先滤一遍减少报错噪音） */
const assignableRoles = computed(() => {
  const mine = roleOf(auth.me?.roleCode);
  if (!mine) return roles.value;
  return roles.value.filter((r) => r.level < mine.level);
});

async function refresh() {
  loading.value = true;
  try {
    const [userPage, roleList] = await Promise.all([
      api("GET", "/api/admin/users?" + new URLSearchParams({
        ...(keyword.value ? { keyword: keyword.value } : {}),
        ...(roleFilter.value ? { roleCode: roleFilter.value } : {}),
        ...(enabledFilter.value !== "" ? { enabled: enabledFilter.value } : {}),
        page: String(page.value),
        size: String(SIZE),
      })),
      api("GET", "/api/admin/roles"),
    ]);
    users.value = userPage.rows || [];
    usersTotal.value = userPage.total || 0;
    roles.value = roleList || [];
    if (!createForm.roleCode && assignableRoles.value.length) {
      createForm.roleCode = assignableRoles.value[0].code;
    }
  } catch (error) {
    ui.toast(error.message || "加载失败", "error");
  } finally {
    loading.value = false;
  }
}

async function loadGrants() {
  loading.value = true;
  try {
    grants.value = (await api("GET", "/api/admin/grants")) || [];
  } catch (error) {
    ui.toast(error.message || "加载失败", "error");
  } finally {
    loading.value = false;
  }
}

function search() {
  page.value = 1;
  refresh();
}

function randomPassword() {
  // 管理员可一键生成强口令；生成发生在本地浏览器，不经任何对话链路
  const alphabet = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789!@#$%";
  const bytes = new Uint32Array(14);
  crypto.getRandomValues(bytes);
  createForm.password = [...bytes].map((n) => alphabet[n % alphabet.length]).join("");
}

async function createUser() {
  if (!createForm.username.trim() || !createForm.password || !createForm.roleCode) {
    ui.toast("用户名、初始密码、角色都需要填写", "error");
    return;
  }
  creating.value = true;
  try {
    await api("POST", "/api/admin/users", {
      username: createForm.username.trim(),
      password: createForm.password,
      roleCode: createForm.roleCode,
      ...(createForm.displayName.trim() ? { displayName: createForm.displayName.trim() } : {}),
    });
    ui.toast(`账号 ${createForm.username.trim()} 已创建`, "success");
    resetCreateForm();
    refresh();
  } catch (error) {
    ui.toast(error.message || "创建失败", "error");
  } finally {
    creating.value = false;
  }
}

async function toggleEnabled(user) {
  const turningOff = user.enabled;
  if (turningOff && !(await ui.ask(
    "停用账号",
    `停用后 ${user.username} 的令牌将立即失效，直到重新启用。确定停用吗？`,
    "停用",
  ))) return;
  try {
    await api("PATCH", `/api/admin/users/${user.id}`, { enabled: !turningOff });
    ui.toast(turningOff ? "已停用" : "已启用", "success");
    refresh();
  } catch (error) {
    ui.toast(error.message || "操作失败", "error");
  }
}

async function changeRole(user, code) {
  if (!code || code === user.roleCode) return;
  if (!(await ui.ask("变更角色", `把 ${user.username} 的角色从「${roleLabel(user.roleCode)}」改为「${roleLabel(code)}」？`, "变更"))) {
    refresh(); // select 回显恢复
    return;
  }
  try {
    await api("PATCH", `/api/admin/users/${user.id}`, { roleCode: code });
    ui.toast("角色已变更", "success");
    refresh();
  } catch (error) {
    ui.toast(error.message || "变更失败", "error");
    refresh();
  }
}

async function resetPassword(user) {
  if (!(await ui.ask("重置密码", `将为 ${user.username} 生成新的初始密码，旧密码立即失效。继续吗？`, "重置"))) return;
  try {
    const result = await api("POST", `/api/admin/users/${user.id}/password`);
    pwResult.value = result;
    copied.value = false;
    refresh();
  } catch (error) {
    ui.toast(error.message || "重置失败", "error");
  }
}

async function copyPw() {
  try {
    await navigator.clipboard.writeText(pwResult.value.initialPassword);
    copied.value = true;
  } catch {
    ui.toast("复制失败，请手动选择复制", "error");
  }
}

function subjectLabel(grant) {
  if (grant.subjectType === "ROLE") {
    const role = roleOf(roleCodeById(grant.subjectId));
    return `角色 ${role ? role.name : "#" + grant.subjectId}`;
  }
  const user = users.value.find((u) => u.id === grant.subjectId);
  return `用户 ${user ? user.username : "#" + grant.subjectId}`;
}
function roleCodeById(id) {
  const role = roles.value.find((r) => r.id === id);
  return role ? role.code : "";
}

const ACCESS_LABELS = { READ: "只读", READ_WRITE: "读写", FULL: "完全" };

async function createGrant() {
  if (!grantForm.subjectId || !grantForm.entity.trim()) {
    ui.toast("请选择授权对象并填写实体表名", "error");
    return;
  }
  granting.value = true;
  try {
    await api("POST", "/api/admin/grants", {
      subjectType: grantForm.subjectType,
      subjectId: grantForm.subjectId,
      entity: grantForm.entity.trim(),
      access: grantForm.access,
      ...(grantForm.expiresAt ? { expiresAt: new Date(grantForm.expiresAt).toISOString() } : {}),
    });
    ui.toast("授权已添加", "success");
    showGrantForm.value = false;
    grantForm.entity = "";
    grantForm.expiresAt = "";
    loadGrants();
  } catch (error) {
    ui.toast(error.message || "授权失败", "error");
  } finally {
    granting.value = false;
  }
}

async function revokeGrant(grant) {
  if (!(await ui.ask("撤销授权", `撤销「${grant.entity}」的${ACCESS_LABELS[grant.access] || grant.access}授权？`, "撤销"))) return;
  try {
    await api("DELETE", `/api/admin/grants/${grant.id}`);
    ui.toast("已撤销", "success");
    loadGrants();
  } catch (error) {
    ui.toast(error.message || "撤销失败", "error");
  }
}

const totalPages = computed(() => Math.max(1, Math.ceil(usersTotal.value / SIZE)));
function goto(delta) {
  const next = page.value + delta;
  if (next < 1 || next > totalPages.value) return;
  page.value = next;
  refresh();
}
</script>

<template>
  <BaseDialog :open="open" @close="ui.closeDialog()">
    <div class="modal-head">
      <div>
        <h2>账号管理</h2>
        <p>账号与授权数据在产物侧；能力校验与操作审计由产物完成</p>
      </div>
      <button class="icon-btn" type="button" aria-label="关闭" @click="ui.closeDialog()">
        <AppIcon name="x" />
      </button>
    </div>

    <div class="panel-tabs">
      <div class="segmented">
        <button :class="{ on: tab === 'users' }" type="button" @click="tab = 'users'">账号</button>
        <button :class="{ on: tab === 'roles' }" type="button" @click="tab = 'roles'">角色</button>
        <button :class="{ on: tab === 'grants' }" type="button" @click="tab = 'grants'">表级授权</button>
      </div>
      <button class="btn-sm ghost" type="button" :disabled="loading" @click="tab === 'grants' ? loadGrants() : refresh()">
        <AppIcon name="refresh" />
      </button>
    </div>

    <div class="modal-body">
      <!-- ======================== 账号 ======================== -->
      <div v-if="tab === 'users'">
        <div v-if="pwResult" class="pw-result">
          <div class="pw-head">
            <AppIcon name="alert" />
            <strong>{{ pwResult.username }} 的新初始密码</strong>
            <button class="icon-btn" type="button" aria-label="关闭" @click="pwResult = null">
              <AppIcon name="x" />
            </button>
          </div>
          <div class="pw-row">
            <code>{{ pwResult.initialPassword }}</code>
            <button class="btn-sm" type="button" @click="copyPw">{{ copied ? "已复制" : "复制" }}</button>
          </div>
          <p class="muted">仅此一次展示，关闭后无法再次查看。请通过安全渠道交给本人，首次登录会要求改密。</p>
        </div>

        <div class="toolbar">
          <input v-model="keyword" class="inp" placeholder="搜索用户名" @keyup.enter="search" />
          <select v-model="roleFilter" class="inp sel" @change="search">
            <option value="">全部角色</option>
            <option v-for="r in roles" :key="r.code" :value="r.code">{{ r.name }}</option>
          </select>
          <select v-model="enabledFilter" class="inp sel narrow" @change="search">
            <option value="">全部状态</option>
            <option value="true">启用</option>
            <option value="false">停用</option>
          </select>
          <button class="btn-sm primary" type="button" @click="showCreate = !showCreate">
            <AppIcon name="plus" />新建账号
          </button>
        </div>

        <div v-if="showCreate" class="create-form">
          <div class="grid2">
            <label class="f"><span>用户名 *</span><input v-model="createForm.username" class="inp" maxlength="64" /></label>
            <label class="f"><span>显示名</span><input v-model="createForm.displayName" class="inp" maxlength="64" /></label>
            <label class="f">
              <span>初始密码 *（≥8 位）</span>
              <div class="pw-input">
                <input v-model="createForm.password" class="inp" maxlength="64" autocomplete="new-password" />
                <button class="btn-sm" type="button" title="生成随机密码" @click="randomPassword">随机</button>
              </div>
            </label>
            <label class="f">
              <span>角色 *</span>
              <select v-model="createForm.roleCode" class="inp sel">
                <option v-for="r in assignableRoles" :key="r.code" :value="r.code">{{ r.name }}（{{ r.code }}）</option>
              </select>
            </label>
          </div>
          <div class="form-actions">
            <button class="btn-sm ghost" type="button" @click="resetCreateForm">取消</button>
            <button class="btn-sm primary" type="button" :disabled="creating" @click="createUser">
              {{ creating ? "创建中…" : "创建" }}
            </button>
          </div>
        </div>

        <table class="tbl">
          <thead>
            <tr><th>用户名</th><th>角色</th><th>状态</th><th>创建时间</th><th>操作</th></tr>
          </thead>
          <tbody>
            <tr v-if="loading"><td colspan="5" class="empty">加载中…</td></tr>
            <tr v-else-if="!users.length"><td colspan="5" class="empty">没有匹配的账号</td></tr>
            <tr v-for="u in users" v-else :key="u.id" :class="{ off: !u.enabled }">
              <td>
                <span class="uname">{{ u.username }}</span>
                <span v-if="u.mustChangePassword" class="badge warn" title="首次登录需改密">待改密</span>
              </td>
              <td>
                <select
                  class="inp sel inline-sel"
                  :value="u.roleCode"
                  :disabled="u.id === auth.me?.id"
                  @change="changeRole(u, $event.target.value)"
                >
                  <option v-for="r in roles" :key="r.code" :value="r.code">{{ r.name }}</option>
                </select>
              </td>
              <td>
                <span class="badge" :class="u.enabled ? 'ok' : 'off-badge'">{{ u.enabled ? "启用" : "停用" }}</span>
              </td>
              <td class="dim">{{ fmtDate(u.createdAt) }}</td>
              <td class="ops">
                <button class="btn-sm" type="button" @click="resetPassword(u)">重置密码</button>
                <button
                  class="btn-sm"
                  :class="u.enabled ? 'danger' : 'primary'"
                  type="button"
                  :disabled="u.id === auth.me?.id"
                  @click="toggleEnabled(u)"
                >
                  {{ u.enabled ? "停用" : "启用" }}
                </button>
              </td>
            </tr>
          </tbody>
        </table>

        <div v-if="usersTotal > SIZE" class="pager">
          <button class="btn-sm ghost" type="button" :disabled="page <= 1" @click="goto(-1)">上一页</button>
          <span class="dim">第 {{ page }} / {{ totalPages }} 页 · 共 {{ usersTotal }} 个账号</span>
          <button class="btn-sm ghost" type="button" :disabled="page >= totalPages" @click="goto(1)">下一页</button>
        </div>
      </div>

      <!-- ======================== 角色 ======================== -->
      <div v-else-if="tab === 'roles'" class="role-list">
        <div v-for="r in roles" :key="r.code" class="role-card">
          <div class="role-head">
            <strong>{{ r.name }}</strong>
            <span class="badge" :class="r.builtin ? 'ok' : ''">{{ r.builtin ? "内置" : "自定义" }}</span>
            <code class="role-code">{{ r.code }}</code>
            <span class="dim">层级 {{ r.level }}</span>
          </div>
          <div class="caps">
            <span v-for="c in r.capabilities || []" :key="c" class="cap">{{ c }}</span>
            <span v-if="!(r.capabilities || []).length" class="dim">无能力声明</span>
          </div>
        </div>
        <p class="muted">角色层级是结构不是配置：只能管理比自己层级低的账号（产物侧强制）。</p>
      </div>

      <!-- ======================== 授权 ======================== -->
      <div v-else class="grant-list">
        <div class="toolbar">
          <span class="muted">共 {{ grants.length }} 条表级授权</span>
          <button class="btn-sm primary" type="button" @click="showGrantForm = !showGrantForm">
            <AppIcon name="plus" />新增授权
          </button>
        </div>

        <div v-if="showGrantForm" class="create-form">
          <div class="grid2">
            <label class="f">
              <span>对象类型 *</span>
              <select v-model="grantForm.subjectType" class="inp sel">
                <option value="ROLE">角色</option>
                <option value="USER">用户</option>
              </select>
            </label>
            <label class="f">
              <span>授权对象 *</span>
              <select v-model.number="grantForm.subjectId" class="inp sel">
                <option :value="null" disabled>请选择</option>
                <template v-if="grantForm.subjectType === 'ROLE'">
                  <option v-for="r in roles" :key="r.id" :value="r.id">{{ r.name }}</option>
                </template>
                <template v-else>
                  <option v-for="u in users" :key="u.id" :value="u.id">{{ u.username }}</option>
                </template>
              </select>
            </label>
            <label class="f"><span>实体表名 *（如 activity）</span><input v-model="grantForm.entity" class="inp" /></label>
            <label class="f">
              <span>访问级别 *</span>
              <select v-model="grantForm.access" class="inp sel">
                <option value="READ">只读</option>
                <option value="READ_WRITE">读写</option>
                <option value="FULL">完全</option>
              </select>
            </label>
            <label class="f"><span>过期时间（可选）</span><input v-model="grantForm.expiresAt" type="datetime-local" class="inp" /></label>
          </div>
          <div class="form-actions">
            <button class="btn-sm ghost" type="button" @click="showGrantForm = false">取消</button>
            <button class="btn-sm primary" type="button" :disabled="granting" @click="createGrant">
              {{ granting ? "提交中…" : "授权" }}
            </button>
          </div>
        </div>

        <table class="tbl">
          <thead>
            <tr><th>对象</th><th>实体</th><th>级别</th><th>过期时间</th><th>操作</th></tr>
          </thead>
          <tbody>
            <tr v-if="loading"><td colspan="5" class="empty">加载中…</td></tr>
            <tr v-else-if="!grants.length"><td colspan="5" class="empty">还没有表级授权</td></tr>
            <tr v-for="g in grants" v-else :key="g.id">
              <td>{{ subjectLabel(g) }}</td>
              <td><code class="entity">{{ g.entity }}</code></td>
              <td>{{ ACCESS_LABELS[g.access] || g.access }}</td>
              <td class="dim">{{ g.expiresAt ? fmtDate(g.expiresAt) : "永久" }}</td>
              <td class="ops">
                <button class="btn-sm danger" type="button" @click="revokeGrant(g)">撤销</button>
              </td>
            </tr>
          </tbody>
        </table>
        <p class="muted">授权刻意没有批量端点（它是「最容易被一句话打穿的面」）；逐条添加，逐条撤销。</p>
      </div>
    </div>
  </BaseDialog>
</template>

<style scoped>
.panel-tabs {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 22px 0;
}

.toolbar { display: flex; align-items: center; gap: 8px; margin: 10px 0 12px; flex-wrap: wrap; }
.toolbar .muted { margin-right: auto; }

.inp {
  height: 32px;
  padding: 0 10px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--surface);
  font-size: 12.5px;
  min-width: 0;
}
.inp:focus { outline: none; border-color: var(--accent); }
.sel { padding-right: 4px; }
.narrow { width: 92px; }
.toolbar .inp:not(.sel) { flex: 1; max-width: 200px; }

.create-form {
  border: 1px solid var(--line);
  border-radius: 11px;
  background: var(--bg-soft);
  padding: 12px;
  margin-bottom: 12px;
}
.grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.f { display: flex; flex-direction: column; gap: 5px; font-size: 12px; color: var(--ink-2); }
.f .inp { width: 100%; }
.pw-input { display: flex; gap: 6px; }
.pw-input .inp { flex: 1; }
.form-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 10px; }

.tbl { width: 100%; border-collapse: collapse; font-size: 12.5px; }
.tbl th {
  text-align: left;
  padding: 7px 8px;
  font-size: 11px;
  color: var(--ink-3);
  border-bottom: 1px solid var(--line);
  font-weight: 600;
}
.tbl td { padding: 8px; border-bottom: 1px solid var(--line); vertical-align: middle; }
.tbl tr:last-child td { border-bottom: 0; }
.tbl tr.off .uname { color: var(--ink-3); text-decoration: line-through; }
.uname { font-weight: 550; }
.dim { color: var(--ink-3); font-size: 11.5px; }
.empty { text-align: center; color: var(--ink-3); padding: 22px 0 !important; }
.ops { display: flex; gap: 6px; white-space: nowrap; }
.inline-sel { height: 28px; font-size: 12px; max-width: 120px; }

.badge {
  display: inline-block;
  margin-left: 6px;
  padding: 1px 7px;
  border-radius: 999px;
  font-size: 10.5px;
  background: var(--surface-2);
  color: var(--ink-2);
}
.badge.ok { background: var(--accent-soft, var(--surface-2)); color: var(--accent); }
.badge.warn { background: var(--warning-soft, var(--surface-2)); color: var(--warning, var(--ink-2)); }
.badge.off-badge { background: var(--danger-soft); color: var(--danger); margin-left: 0; }

.pager { display: flex; align-items: center; justify-content: center; gap: 12px; padding: 12px 0 4px; }

.pw-result {
  border: 1px solid var(--line);
  border-left: 3px solid var(--warning, var(--accent));
  border-radius: 11px;
  background: var(--bg-soft);
  padding: 12px;
  margin: 10px 0 12px;
}
.pw-head { display: flex; align-items: center; gap: 8px; font-size: 13px; }
.pw-head .icon-btn { margin-left: auto; width: 26px; height: 26px; }
.pw-row { display: flex; align-items: center; gap: 10px; margin: 10px 0 6px; }
.pw-row code {
  font-family: var(--mono);
  font-size: 14px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 7px;
  padding: 5px 10px;
  user-select: all;
}

.role-list { display: flex; flex-direction: column; gap: 10px; padding-top: 12px; }
.role-card { border: 1px solid var(--line); border-radius: 11px; padding: 12px 14px; }
.role-head { display: flex; align-items: center; gap: 10px; }
.role-code { font-family: var(--mono); font-size: 11.5px; color: var(--ink-3); }
.caps { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 9px; }
.cap {
  font-family: var(--mono);
  font-size: 11px;
  background: var(--surface-2);
  border-radius: 6px;
  padding: 2px 7px;
  color: var(--ink-2);
}

.grant-list { padding-top: 4px; }
.entity { font-family: var(--mono); font-size: 12px; }

@media (max-width: 640px) {
  .grid2 { grid-template-columns: 1fr; }
}
</style>
