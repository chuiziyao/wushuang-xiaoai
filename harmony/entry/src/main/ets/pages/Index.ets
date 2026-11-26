import { webview } from '@kit.ArkWeb';
import { preferences } from '@kit.ArkData';
import { common } from '@kit.AbilityKit';
import { promptAction } from '@kit.ArkUI';

/**
 * 无双遥控器 鸿蒙版：WebView 直连无双小爱同学后台。
 * 首次启动填写服务地址（电脑/NAS 局域网地址），持久保存后直连。
 */
@Entry
@Component
struct Index {
  controller: webview.WebviewController = new webview.WebviewController();
  @State serverUrl: string = '';
  @State inputUrl: string = '';
  @State ready: boolean = false;
  private context = getContext(this) as common.UIAbilityContext;

  async aboutToAppear(): Promise<void> {
    try {
      const store = await preferences.getPreferences(this.context, 'wushuang');
      const saved = (await store.get('server_url', '')) as string;
      if (saved) {
        this.serverUrl = saved;
        this.inputUrl = saved;
        this.ready = true;
      }
    } catch (e) {
      console.error('读取配置失败: ' + JSON.stringify(e));
    }
  }

  async connect(): Promise<void> {
    let u = this.inputUrl.trim();
    if (!u) {
      promptAction.showToast({ message: '请填写服务地址，如 http://192.168.1.10:8080' });
      return;
    }
    if (!u.startsWith('http://') && !u.startsWith('https://')) {
      u = 'http://' + u;
    }
    u = u.replace(/\/+$/, '');
    const store = await preferences.getPreferences(this.context, 'wushuang');
    await store.put('server_url', u);
    await store.flush();
    this.serverUrl = u;
    this.ready = true;
  }

  onBackPress(): boolean {
    if (this.ready && this.controller.accessBackward()) {
      this.controller.backward();
      return true;
    }
    return false;
  }

  build() {
    Column() {
      if (!this.ready) {
        Column({ space: 16 }) {
          Text('🎵 无双遥控器').fontSize(28).fontWeight(FontWeight.Bold)
          Text('连接电脑/NAS 上的「无双小爱同学」服务')
            .fontSize(14).fontColor('#8a94a3')
          TextInput({ placeholder: 'http://192.168.1.10:8080', text: this.inputUrl })
            .type(InputType.URL)
            .onChange((v: string) => { this.inputUrl = v; })
            .width('90%')
          Button('连接')
            .width('90%')
            .backgroundColor('#1a9e5c')
            .onClick(() => { this.connect(); })
          Text('作者：锤无双 · QQ群 170621740')
            .fontSize(12).fontColor('#8a94a3').margin({ top: 24 })
        }
        .width('100%').height('100%')
        .justifyContent(FlexAlign.Center)
      } else {
        Row({ space: 8 }) {
          Text(this.serverUrl)
            .fontSize(12).fontColor('#8a94a3')
            .layoutWeight(1)
            .maxLines(1).textOverflow({ overflow: TextOverflow.Ellipsis })
          Button('切换服务')
            .fontSize(12).height(28)
            .backgroundColor('#eef1f4').fontColor('#1c2431')
            .onClick(() => { this.ready = false; })
        }
        .width('100%').padding(8)

        Web({ src: this.serverUrl, controller: this.controller })
          .javaScriptAccess(true)
          .domStorageAccess(true)
          .mixedMode(MixedMode.All)
          .fileAccess(false)
          .onlineImageAccess(true)
          .layoutWeight(1)
          .onErrorReceive((event) => {
            if (event) {
              console.error('页面加载错误: ' + event.error.getErrorInfo());
            }
          })
      }
    }
    .width('100%').height('100%')
  }
}