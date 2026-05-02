<script setup lang="ts">
import { onMounted, ref } from "vue";
import { http } from "../api/http";

const items = ref<Record<string, unknown>[]>([]);
const note = ref("");

onMounted(() => {
  void load();
});

async function load() {
  const r = await http.get("/api/exports/artifacts");
  items.value = r.data.items;
  note.value = r.data.note;
}
</script>

<template>
  <div>
    <a-typography-title :level="4">导出 / 产物</a-typography-title>
    <a-alert v-if="note" type="info" :message="note" show-icon style="margin-bottom: 12px" />
    <a-list :data-source="items" bordered>
      <template #renderItem="{ item }">
        <a-list-item>
          <a-list-item-meta
            :title="String((item as { label: string }).label || (item as { path: string }).path)"
            :description="`${(item as { kind: string }).kind} — ${(item as { path: string }).path}`"
          />
        </a-list-item>
      </template>
    </a-list>
  </div>
</template>
