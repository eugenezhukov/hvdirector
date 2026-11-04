import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import * as Icons from '@element-plus/icons-vue'
import App from './App.vue'
import { router } from './router'
import { i18n } from './i18n'
import './styles.css'

const app = createApp(App)
for (const [name, comp] of Object.entries(Icons)) app.component(`Icon${name}`, comp)
app.use(createPinia())
app.use(i18n)
app.use(ElementPlus)
app.use(router)
app.mount('#app')
