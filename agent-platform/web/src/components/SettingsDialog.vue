<script setup>
import { computed, ref, watch } from "vue";
import AppIcon from "./AppIcon.vue";
import BaseDialog from "./BaseDialog.vue";
import { useAuthStore } from "../stores/auth";
import { useChatStore } from "../stores/chat";
import { useSettingsStore } from "../stores/settings";
import { useUiStore } from "../stores/ui";

const auth = useAuthStore();
const chat = useChatStore();
const settings = useSettingsStore();
const ui = useUiStore();

const open = computed(() => ui.dialog === "settings");
const showKey = ref(false);
const editable = computed(() => auth.canEditLlm && !chat.demo);

// 打开时刷一次平台状态，避免展示上一次会话的旧值
watch(open, (value) => {
  if (!value) return;
  showKey.value = false;
  settings.llmStatus = { text: "", tone: "" };
  if (auth.canEditLlm && !chat.demo) settings.loadServerStatus();
});

const themes = [
  { value: "light", label: "浅色" },
  { value: "dark", label: "深色" },
  { value: "system", label: "跟随系统" },
];
const fonts = [
  { value: "sm", label: "小" },
  { value: "md", label: "标准" },
  { value: "lg", label: "大" },
];
const sendKeys = [
  { value: "enter", label: "Enter" },
  { value: "mod", label: "⌘ + Enter" },
];

const keyHint = computed(() => {
  if (chat.demo) return "演示模式不写服务端";
  if (!auth.canEditLlm) return "当前账号无权修改（仅管理员）";
  if (settings.llm.model) return "已从当前浏览器恢复上次填写的内容";
  return settings.serverConfigured ? "未在此浏览器填写；平台已有默认配置" : "填写你自己的模型服务信息";
});
</script>

<template>
  <BaseDialog :open="open" @close="ui.closeDialog()">
    <div class="modal-head">
      <div>
        <h2>设置</h2>
        <p>模型信息由你填写并缓存在本浏览器；保存后平台全局生效</p>
      </div>
      <button class="icon-btn" type="button" aria-label="关闭" @click="ui.closeDialog()">
        <AppIcon name="x" />
      </button>
    </div>

    <div class="modal-body">
      <section class="setting-group">
        <h3>模型服务</h3>
        <div class="model-head">
          <div class="setting-label">
            <strong>自定义模型接入</strong>
            <small>{{ keyHint }}</small>
          </div>
          <span class="model-global">全局生效</span>
        </div>

        <div class="llm-form">
          <label class="llm-field">
            <span>API Base URL</span>
            <input
              v-model="settings.llm.baseUrl"
              type="url"
              placeholder="https://api.example.com/v1"
              autocomplete="off"
              spellcheck="false"
              :disabled="!editable || settings.llmBusy"
            />
            <em>填到 /v1 为止，平台自动拼接 /chat/completions</em>
          </label>

          <label class="llm-field">
            <span>
              API Key
              <small>{{ settings.llm.apiKey ? "已缓存" : "未填写" }}</small>
            </span>
            <div class="key-wrap">
              <input
                v-model="settings.llm.apiKey"
                :type="showKey ? 'text' : 'password'"
                placeholder="请输入 API Key"
                autocomplete="new-password"
                spellcheck="false"
                :disabled="!editable || settings.llmBusy"
              />
              <button class="key-toggle" type="button" @click="showKey = !showKey">
                {{ showKey ? "隐藏" : "显示" }}
              </button>
            </div>
          </label>

          <label class="llm-field">
            <span>模型名</span>
            <input
              v-model="settings.llm.model"
              type="text"
              placeholder="deepseek-chat / kimi-k2 / glm-4.5 / qwen-max"
              autocomplete="off"
              spellcheck="false"
              :disabled="!editable || settings.llmBusy"
            />
          </label>
        </div>

        <div class="llm-actions">
          <button class="btn-sm" type="button" :disabled="!editable || settings.llmBusy" @click="settings.testLlm()">
            测试连接
          </button>
          <button class="btn-sm primary" type="button" :disabled="!editable || settings.llmBusy" @click="settings.saveLlm()">
            保存并应用
          </button>
          <span class="llm-status" :class="settings.llmStatus.tone">{{ settings.llmStatus.text }}</span>
        </div>

        <div class="llm-foot-row">
          <p class="model-foot">配置缓存在当前浏览器；API Key 也用于本机调用，公共电脑请用完清除。</p>
          <button class="link-btn" type="button" @click="settings.clearLlmCache()">清除本机缓存</button>
        </div>
      </section>

      <section class="setting-group">
        <h3>外观</h3>
        <div class="setting-row">
          <div class="setting-label"><strong>主题</strong><small>跟随系统时会随系统深浅色自动切换</small></div>
          <div class="segmented" role="group" aria-label="主题">
            <button
              v-for="item in themes"
              :key="item.value"
              type="button"
              :class="{ on: settings.theme === item.value }"
              @click="settings.set('theme', item.value)"
            >
              {{ item.label }}
            </button>
          </div>
        </div>
        <div class="setting-row">
          <div class="setting-label"><strong>字号</strong><small>影响对话正文与表格文字</small></div>
          <div class="segmented" role="group" aria-label="字号">
            <button
              v-for="item in fonts"
              :key="item.value"
              type="button"
              :class="{ on: settings.fontSize === item.value }"
              @click="settings.set('fontSize', item.value)"
            >
              {{ item.label }}
            </button>
          </div>
        </div>
      </section>

      <section class="setting-group">
        <h3>对话</h3>
        <div class="setting-row">
          <div class="setting-label">
            <strong>显示工具调用过程</strong>
            <small>展示 query_records / aggregate_records 等执行轨迹</small>
          </div>
          <button
            class="switch"
            type="button"
            role="switch"
            :aria-checked="settings.showTools"
            @click="settings.set('showTools', !settings.showTools)"
          ><span></span></button>
        </div>
        <div class="setting-row">
          <div class="setting-label"><strong>发送方式</strong><small>使用 ⌘ + Enter 时，Enter 用于换行</small></div>
          <div class="segmented" role="group" aria-label="发送方式">
            <button
              v-for="item in sendKeys"
              :key="item.value"
              type="button"
              :class="{ on: settings.sendKey === item.value }"
              @click="settings.set('sendKey', item.value)"
            >
              {{ item.label }}
            </button>
          </div>
        </div>
      </section>

      <section class="setting-group">
        <h3>关于</h3>
        <div class="about-grid">
          <div><span>平台版本</span><strong>SysCraft 0.2.0</strong></div>
          <div><span>前端</span><strong>Vue 3 + Vite + ECharts</strong></div>
          <div><span>当前账号</span><strong>{{ auth.name }} · {{ auth.roleLabel }}</strong></div>
        </div>
      </section>
    </div>

    <div class="modal-foot">
      <button class="btn-ghost" type="button" @click="settings.reset()">恢复界面默认</button>
      <button class="btn-primary" type="button" @click="ui.closeDialog()">完成</button>
    </div>
  </BaseDialog>
</template>

<style scoped>
.model-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; padding: 12px 0 2px; }
.model-global {
  flex: none;
  padding: 2px 7px;
  border-radius: 999px;
  background: var(--surface-2);
  color: var(--ink-3);
  font-size: 10px;
  line-height: 1.6;
}
.llm-form { display: flex; flex-direction: column; gap: 11px; padding: 10px 0 2px; }
.llm-field { display: flex; flex-direction: column; gap: 5px; min-width: 0; }
.llm-field > span { display: flex; align-items: center; gap: 8px; font-size: 11.5px; color: var(--ink-2); }
.llm-field > span small { font-size: 10.5px; font-weight: 400; color: var(--ink-3); }
.llm-field input {
  width: 100%;
  height: 34px;
  padding: 0 10px;
  border: 1px solid var(--line-strong);
  border-radius: 8px;
  background: var(--bg);
  color: var(--ink);
  font-size: 12.5px;
  outline: none;
  transition: border-color .15s, box-shadow .15s;
}
.llm-field input:focus { border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft); }
.llm-field input:disabled { opacity: .6; cursor: not-allowed; background: var(--surface-2); }
.llm-field input::placeholder { color: var(--ink-3); }
.llm-field em { font-style: normal; font-size: 10.5px; color: var(--ink-3); }
.key-wrap { position: relative; }
.key-wrap input { padding-right: 58px; }
.key-toggle {
  position: absolute;
  top: 50%;
  right: 4px;
  height: 26px;
  padding: 0 8px;
  border-radius: 6px;
  color: var(--ink-2);
  font-size: 11px;
  transform: translateY(-50%);
  transition: background .12s, color .12s;
}
.key-toggle:hover { background: var(--surface-2); color: var(--ink); }

.llm-actions { display: flex; align-items: center; gap: 8px; padding: 9px 0 2px; flex-wrap: wrap; }
.llm-status { font-size: 11px; line-height: 1.5; color: var(--ink-3); }
.llm-status.ok { color: var(--ok); }
.llm-status.fail { color: var(--danger); }
.llm-foot-row { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.model-foot { flex: 1; margin: 4px 0 0; font-size: 10.5px; line-height: 1.6; color: var(--ink-3); }

.about-grid { display: flex; flex-direction: column; gap: 1px; padding: 8px 0 4px; }
.about-grid > div {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  padding: 8px 0;
  border-bottom: 1px solid var(--line);
  font-size: 12.5px;
}
.about-grid > div:last-child { border-bottom: 0; }
.about-grid span { color: var(--ink-3); }
.about-grid strong { font-weight: 550; }
</style>
