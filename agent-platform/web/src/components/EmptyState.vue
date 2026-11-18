<script setup>
import AppIcon from "./AppIcon.vue";

defineEmits(["pick"]);

const suggestions = [
  { icon: "table", title: "看看现在有哪些数据", prompt: "查一下活动有多少条" },
  { icon: "chart", title: "按状态做一次统计", prompt: "按报名状态统计报名记录" },
  { icon: "edit", title: "登记一条新记录", prompt: "新增一条报名记录" },
  { icon: "spark", title: "生成一个新系统", prompt: "做一个图书借阅管理系统" },
];
</script>

<template>
  <section class="empty-state">
    <div class="logo"><span class="brand-mark brand-mark-xl">S</span></div>
    <h1>今天想聊点什么？</h1>
    <p>查询业务数据，或直接描述一个要生成的系统。</p>

    <div class="suggestions">
      <button
        v-for="item in suggestions"
        :key="item.prompt"
        class="suggestion"
        type="button"
        @click="$emit('pick', item.prompt)"
      >
        <span class="s-ico"><AppIcon :name="item.icon" /></span>
        <span class="s-text">
          <strong>{{ item.title }}</strong>
          <small>{{ item.prompt }}</small>
        </span>
      </button>
    </div>
  </section>
</template>

<style scoped>
.empty-state {
  max-width: 780px;
  margin: 0 auto;
  padding: 0 24px 30px;
  text-align: center;
  width: 100%;
  animation: rise .35s ease-out;
}
.logo { display: flex; justify-content: center; margin-bottom: 18px; }
.logo .brand-mark { transform: rotate(-4deg); }
h1 {
  font-size: calc(29px * var(--scale));
  font-weight: 650;
  letter-spacing: -.025em;
  margin: 0 0 8px;
}
p { margin: 0 0 26px; color: var(--ink-2); font-size: calc(14px * var(--scale)); }

.suggestions { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; text-align: left; }
.suggestion {
  display: flex;
  gap: 11px;
  align-items: flex-start;
  padding: 13px 14px;
  border: 1px solid var(--line);
  border-radius: 13px;
  background: var(--surface);
  text-align: left;
  transition: border-color .15s, box-shadow .15s, transform .15s;
}
.suggestion:hover { border-color: var(--line-strong); box-shadow: var(--shadow-md); transform: translateY(-1px); }
.s-ico {
  width: 30px;
  height: 30px;
  flex: none;
  display: grid;
  place-items: center;
  border-radius: 8px;
  background: var(--surface-2);
  color: var(--ink-2);
}
.s-ico .i { width: 16px; height: 16px; }
.s-text { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.s-text strong { font-size: 13px; font-weight: 600; }
.s-text small {
  font-size: 11.5px;
  color: var(--ink-3);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
