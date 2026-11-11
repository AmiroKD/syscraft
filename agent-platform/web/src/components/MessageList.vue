<script setup>
import MessageItem from "./MessageItem.vue";
import { useChatStore } from "../stores/chat";
import { useSettingsStore } from "../stores/settings";

const chat = useChatStore();
const settings = useSettingsStore();

function isLive(message) {
  // 「当前这一轮」的卡片才带确认按钮：历史（turn=0）里的确认令牌早过期了
  return message.turn > 0 && message.turn === chat.turn;
}
</script>

<template>
  <div class="thread">
    <template v-for="message in chat.messages" :key="message.id">
      <div v-if="message.type === 'tools' && !settings.showTools" class="hidden-tools"></div>
      <MessageItem
        v-else
        :message="message"
        :live="isLive(message)"
      />
    </template>

    <div v-if="chat.typing" class="msg assistant">
      <div class="body">
        <span class="typing"><i></i><i></i><i></i></span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.thread {
  max-width: 780px;
  margin: 0 auto;
  padding: 22px 24px 10px;
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.hidden-tools { display: none; }

.msg { display: flex; flex-direction: column; }
.msg.assistant .body { display: block; }
.typing { display: inline-flex; gap: 4px; align-items: center; height: 22px; }
.typing i {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--ink-3);
  animation: blink 1.2s infinite;
}
.typing i:nth-child(2) { animation-delay: .15s; }
.typing i:nth-child(3) { animation-delay: .3s; }
</style>
