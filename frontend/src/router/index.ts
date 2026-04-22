import { createRouter, createWebHistory } from "vue-router";
import Datasets from "../views/Datasets.vue";
import DataImport from "../views/DataImport.vue";
import Eval from "../views/Eval.vue";
import Export from "../views/Export.vue";
import Home from "../views/Home.vue";
import Playground from "../views/Playground.vue";
import Merge from "../views/Merge.vue";
import Training from "../views/Training.vue";

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", name: "home", component: Home, meta: { title: "设置" } },
    { path: "/import", name: "import", component: DataImport, meta: { title: "数据导入" } },
    { path: "/datasets", name: "datasets", component: Datasets, meta: { title: "数据集" } },
    { path: "/train", name: "train", component: Training, meta: { title: "训练" } },
    { path: "/playground", name: "playground", component: Playground, meta: { title: "推理沙盒" } },
    { path: "/merge", name: "merge", component: Merge, meta: { title: "LoRA 合并" } },
    { path: "/eval", name: "eval", component: Eval, meta: { title: "评测" } },
    { path: "/export", name: "export", component: Export, meta: { title: "导出" } },
  ],
});

router.afterEach((to) => {
  document.title = `${String(to.meta.title ?? "数据工坊")} · 数据工坊`;
});

export default router;
