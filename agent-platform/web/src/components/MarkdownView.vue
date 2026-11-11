<script setup>
import { computed } from "vue";
import { renderMarkdown } from "../lib/markdown";

const props = defineProps({
  text: { type: String, default: "" },
});

const html = computed(() => renderMarkdown(props.text));
</script>

<template>
  <div class="md" v-html="html"></div>
</template>
