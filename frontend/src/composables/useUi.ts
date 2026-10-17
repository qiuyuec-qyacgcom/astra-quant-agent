/** 全局 UI 浮层状态：关于弹窗 / 决策透视抽屉 / 决策轨迹 */
import { ref } from 'vue';

export const aboutOpen = ref(false);
export const peekOpen = ref(false);
export const trajectoryOpen = ref(false);

export function useUi() {
  return { aboutOpen, peekOpen, trajectoryOpen };
}
