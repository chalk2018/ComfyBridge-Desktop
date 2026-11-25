# -*- coding: utf-8 -*-
"""一次性生成器：为提示词模板库灌入 1000 条提示词 + 配套标签。

数据落盘到 data/prompt_templates/，格式与 app/prompttemplates.py 完全一致：
  - 标签：<!-- CBTAG id="..." color="#......" -->名称<!-- CBTAG-END -->
  - 模板：<!-- CBTPL id="..." --> / title/tags/created/updated / 波浪围栏 / 正文 / 围栏 / END

三类分配（合计 1000）：
  - 视频 350（MiniMax H3：文生/首尾帧/参考/故事板 + 运镜/景别/光线/声音/时长）
  - 图片 350（通义千问 Qwen-Image：写实/动漫/油画/水墨/赛博朋克……多风格 × 多主题）
  - 音频 300（MiniMax Music / 天工 yue2：曲风 × 情绪 × 乐器 × 人声 × BPM × 段落）

生成结果 determinstic（固定 seed），可重复运行覆盖。
"""
from __future__ import annotations

import json
import random
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = ROOT / "data" / "prompt_templates"
random.seed(10606)

NOW = "2026-11-22 20:00:00"

# ---------------------------------------------------------------- 标签库
# (id, 名称, 颜色) —— 颜色不重复
TAGS = [
    # 通用/人群
    ("tag_role", "角色", "#e0523a"), ("tag_scene", "场景", "#2f6bff"),
    ("tag_prop", "道具", "#e8a33a"), ("tag_board", "故事板", "#7c6bd6"),
    ("tag_firstlast", "首尾帧", "#2fa36b"), ("tag_swap", "角色替换", "#d4509a"),
    ("tag_music", "音乐复刻", "#0f9aa8"), ("tag_voice", "声音克隆", "#8a6b3f"),
    # 视频风格/技法
    ("tag_v_t2v", "文生视频", "#3f7d8c"), ("tag_v_fl2v", "首尾帧", "#b5533d"),
    ("tag_v_ref2v", "参考生视频", "#5b61c7"), ("tag_v_board", "故事板生视频", "#4a8b3a"),
    ("tag_v_camera", "运镜", "#c27a17"), ("tag_v_cine", "电影感", "#9a4f7d"),
    ("tag_v_doc", "纪实", "#167d70"), ("tag_v_ad", "广告产品", "#5d6b8a"),
    ("tag_v_nature", "自然风光", "#2e8b57"), ("tag_v_urban", "城市街景", "#c0392b"),
    ("tag_v_emotion", "情绪特写", "#8e44ad"), ("tag_v_action", "动作打斗", "#d35400"),
    ("tag_v_slowmo", "慢动作", "#2980b9"), ("tag_v_timelapse", "延时", "#7f8c8d"),
    ("tag_v_3d", "3D动画", "#16a085"), ("tag_v_anime", "动漫风", "#e67e22"),
    ("tag_v_pixel", "像素风", "#34495e"), ("tag_v_noir", "黑白", "#2c3e50"),
    # 图片风格
    ("tag_i_photo", "写实摄影", "#1f618d"), ("tag_i_oil", "油画", "#9c640c"),
    ("tag_i_water", "水彩", "#48c9b0"), ("tag_i_ink", "水墨", "#4d5656"),
    ("tag_i_anime", "日系动漫", "#e84393"), ("tag_i_cyber", "赛博朋克", "#8e44ad"),
    ("tag_i_3d", "3D渲染", "#2980b9"), ("tag_i_pixel", "像素艺术", "#7f8c8d"),
    ("tag_i_min", "极简", "#bdc3c7"), ("tag_i_concept", "游戏概念", "#d35400"),
    ("tag_i_gongbi", "工笔", "#a93226"), ("tag_i_clay", "黏土", "#d68910"),
    ("tag_i_retro", "复古", "#6c5ce7"), ("tag_i_fashion", "时尚大片", "#e17055"),
    ("tag_i_food", "美食", "#00b894"), ("tag_i_animal", "动物", "#55efc4"),
    # 音频风格
    ("tag_a_pop", "流行", "#fd79a8"), ("tag_a_folk", "民谣", "#fdcb6e"),
    ("tag_a_rock", "摇滚", "#d63031"), ("tag_a_electronic", "电子", "#0984e3"),
    ("tag_a_classical", "古典", "#6c5ce7"), ("tag_a_jazz", "爵士", "#00cec9"),
    ("tag_a_guofeng", "国风", "#e17055"), ("tag_a_hiphop", "嘻哈说唱", "#2d3436"),
    ("tag_a_lofi", "Lo-fi", "#a29bfe"), ("tag_a_ambient", "氛围纯音乐", "#81ecec"),
    ("tag_a_sad", "伤感", "#74b9ff"), ("tag_a_happy", "欢快", "#ffeaa7"),
    ("tag_a_epic", "史诗", "#b2bec3"), ("tag_a_chill", "治愈放松", "#55efc4"),
    ("tag_a_vocal", "有人声", "#fab1a0"), ("tag_a_inst", "纯音乐", "#00b894"),
    ("tag_a_cinematic", "影视配乐", "#636e72"), ("tag_a_minimax", "MiniMax", "#0984e3"),
    ("tag_a_yue2", "天工Yue2", "#00b894"),
]

# ---------------------------------------------------------------- 素材库
# 视频主题（主体 + 动作/环境）
V_SCENES = [
    "雨夜霓虹街道，行人撑伞匆匆走过，路面倒映五彩灯牌",
    "清晨山谷薄雾流动，阳光穿透云层洒下光柱，林间光影晃动",
    "海边悬崖，浪花拍打礁石，海鸥盘旋，远处灯塔矗立",
    "未来城市上空，悬浮车穿梭于摩天楼之间，全息广告闪烁",
    "古风竹林，剑客挥剑，竹叶纷飞，阳光透过叶隙形成光斑",
    "繁华夜市，摊位蒸汽升腾，人群熙攘，灯笼串随风轻摆",
    "雪原森林，雪花飘落，一只狐狸在雪地里奔跑留下脚印",
    "沙漠日落，沙丘连绵，驼队缓缓行进，长影拖在沙面上",
    "咖啡馆窗边，女孩看书，阳光洒在侧脸，热气从杯中升起",
    "宇宙空间站舷窗外，地球缓缓转动，宇航员漂浮 passing by",
    "赛博朋克巷道，机械臂维修机器人，霓虹在水洼中闪烁",
    "春日樱花树下，花瓣飘落，少女转身微笑，发丝随风",
    "深海之下，水母群发光漂浮，光线从海面透下",
    "城市天际线延时，从黄昏到夜晚，车流灯光汇成河流",
    "废弃工厂，机械巨兽苏醒，灰尘震落，红色核心发光",
    "古镇石桥，乌篷船划过水面，涟漪扩散，炊烟袅袅",
    "极光下的雪原，绿色极光舞动，驯鹿剪影走过",
    "健身房内，运动员举重，汗水飞溅，肌肉线条分明",
    "花田之中，蜜蜂采蜜，蝴蝶飞舞，风吹花浪起伏",
    "暴雨中的城市路口，闪电划破天际，雨水横流",
]
V_LENS = [
    "85mm f/1.4 浅景深，Dolly-in 极慢推进",
    "14mm 广角 f/8，无人机俯冲后拉升",
    "50mm f/4，环绕主体 360° 匀速旋转",
    "35mm f/2.8，手持跟拍轻微晃动",
    "24mm f/2.8，希区柯克变焦 Dolly Zoom",
    "85mm f/1.8，固定机位缓慢推近面部特写",
    "24-120mm 变焦，从大远景缓缓推至中景",
    "微距 100mm，焦点从前景切换到背景",
    "50mm，低角度仰拍缓慢上摇",
    "35mm，滑轨横移 Slither 匀速",
]
V_LIGHT = [
    "主光左侧45°柔光，色温4000K，光比4:1",
    "逆光剪影，边缘轮廓光，暖金色温3200K",
    "顶光聚光，硬光高反差8:1，暗部深邃",
    "环境漫射光，柔和通透，低对比",
    "霓虹散射光，青紫撞色，湿地面反光",
    "晨光侧射，丁达尔体积光穿过雾气",
    "烛火暖光，明暗跳动，氛围感强",
    "阴天漫射，冷蓝色调6500K，柔和均匀",
]
V_SOUND = [
    "环境音：微风与远处鸟鸣，无背景音乐",
    "雨声均匀覆盖左右声道，远处车流低频轰鸣",
    "安静室内微弱底噪，人物轻微呼吸声",
    "节奏性电子BGM，鼓点清晰，空间感强",
    "海浪声由远及近，海鸥鸣叫，自然白噪音",
    "脚步声从正前方靠近，定位精确，轻微回声",
    "人群嘈杂声全方位环绕，叫卖声左右交替",
    "无声音，纯画面叙事",
]
V_STYLE = [
    "电影质感，青橙调色，胶片颗粒，宽银幕构图",
    "纪录片写实风格，自然光，真实质感",
    "3D 动画电影风格，通透全局光，色彩明快",
    "日系动漫赛璐璐风格，线条清晰色彩柔和",
    "赛博朋克高饱和，霓虹青紫撞色，高科技低生活",
    "黑白电影 Noir 风格，高反差，戏剧性光影",
    "像素艺术风格，硬边锯齿，有限色板",
    "中国古风水墨渲染，留白讲究，意境悠远",
]
V_DUR = [4, 5, 6, 8, 10, 12]

# 图片主题
I_SUBJECTS = [
    "一只橘色英国短毛猫，在窗台上晒太阳，毛发光泽，眼睛明亮",
    "年轻女子，白色长裙，站在花海中，微风吹拂发丝",
    "未来城市天际线，悬浮建筑，空中花园，夕阳染红云层",
    "中式古镇石桥流水，乌篷船，白墙黛瓦，晨雾缭绕",
    "一只穿西装的狐狸，戴单片眼镜，在图书馆喝茶，拟人化",
    "宇航员站在外星草原，身后三颗卫星的紫色星球",
    "一杯冒热气的咖啡，拉花是猫的图案，木质桌面",
    "敦煌飞天仙女，飘逸丝带，祥云中飞舞，壁画质感",
    "机械巨龙盘踞废弃都市，齿轮转动，蒸汽朋克",
    "雪中的梅花，花瓣如丝，露珠轻挂，工笔细腻",
    "现代极简客厅，落地窗外是森林，阳光明媚",
    "熊猫武士，手持竹枪，站在长城烽火台，云海翻腾",
    "水晶与藤蔓构成的独角兽，星河中奔跑，彩虹轨迹",
    "海边咖啡馆外，明亮白背景，梦幻水彩质感",
    "陶瓷小狗静卧桌上，颈系铃铛，毛fur雕刻精致",
    "深海之中，发光水母群，光线从海面透下",
    "复古照片，老式收音机与绿植，暖色调静物",
    "时尚大片，模特五官立体，高级灰背景，硬光塑形",
    "美食特写，松软面包与浆果，晨光，微距镜头",
    "山水之间一叶扁舟，远山若隐若现，意境悠远",
]
I_STYLES = [
    ("写实摄影", "写实摄影，中画幅相机质感，柔和自然光，真实材质纹理，8K高清，细节丰富", "tag_i_photo"),
    ("电影剧照", "电影剧照质感，宽银幕构图，戏剧性布光，青橙电影调色，暗部深邃，胶片颗粒", "tag_v_cine"),
    ("3D动画", "三维动画电影风格，柔和通透全局光，材质干净有质感，色彩明快，C4D渲染", "tag_i_3d"),
    ("游戏概念", "游戏概念设定插画，数字厚涂笔触明显，构图有气氛图气质，冷灰调带色彩点缀", "tag_i_concept"),
    ("水墨淡彩", "东方水墨淡彩，宣纸质感，墨色浓淡晕染，留白讲究，意境悠远", "tag_i_ink"),
    ("水彩", "清新水彩，颜色通透有晕染，笔触松散自然，纸张纹理明显，吉卜力风格", "tag_i_water"),
    ("古典油画", "古典油画，厚涂笔触，色彩浓郁沉稳，明暗对比强烈，画布纹理", "tag_i_oil"),
    ("赛博朋克", "赛博朋克风格，霓虹灯管与全息投影，青紫高对比，雨夜湿地面反光，高科技低生活", "tag_i_cyber"),
    ("极简插画", "极简扁平插画，纯色块，干净构图，低饱和莫兰迪配色", "tag_i_min"),
    ("日系动漫", "日系动漫风格，赛璐璐上色，线条清晰，色彩明快柔和，新海诚风格", "tag_i_anime"),
    ("像素艺术", "复古8-bit像素艺术，缩小到64×64再放大，硬边像素，有限色板，禁止平滑渐变", "tag_i_pixel"),
    ("国风水墨", "中国传统水墨，写意笔法，黑白灰层次，留白与飞白讲究，古朴意境", "tag_i_ink"),
    ("工笔重彩", "中国工笔画，精细线条，层层渲染，色彩华丽，细节繁复精致", "tag_i_gongbi"),
    ("黏土定格", "黏土风格，手工捏制质感， tactile 触感，可爱圆润，工作室灯光", "tag_i_clay"),
    ("蒸汽朋克", "蒸汽朋克风格，黄铜齿轮，机械结构，复古未来主义，暖褐色调", "tag_i_retro"),
    ("时尚摄影", "时尚杂志大片，专业人像打光，浅景深，眼神光，高级质感，极简背景", "tag_i_fashion"),
    ("超现实", "超现实主义，梦境般的不可能场景，柔和光影，精致细节，达利风格", "tag_v_cine"),
    ("点彩派", "点彩派风格，印象派色点，笔触清晰，莫奈感觉，低饱和莫兰迪色", "tag_i_oil"),
    ("折纸艺术", "折纸杰作，牛皮纸质感，几何折叠，极简主义，背光，最佳画质", "tag_i_min"),
    ("陶瓷工艺", "陶瓷雕塑质感，釉面光泽，精致雕刻，细节逼真，静物摄影", "tag_i_clay"),
]
I_LIGHT = ["自然光，黄金时刻", "逆光剪影，轮廓光", "柔光箱棚拍", "霓虹灯光", "体积光丁达尔效应", "清晨柔光", "戏剧性明暗对比", "环境漫射光"]

# 音频素材
A_GENRES = [
    ("华语抒情流行", "tag_a_pop", [72, 76, 80, 84]),
    ("清新独立流行", "tag_a_pop", [100, 105, 110, 112]),
    ("温暖民谣", "tag_a_folk", [70, 74, 78, 82]),
    ("热血摇滚", "tag_a_rock", [130, 140, 145, 150]),
    ("电子舞曲 EDM", "tag_a_electronic", [120, 126, 128, 132]),
    ("Synthwave 复古合成器", "tag_a_electronic", [105, 110, 115, 118]),
    ("Lo-fi Hip Hop", "tag_a_lofi", [75, 80, 85, 90]),
    ("古典钢琴独奏", "tag_a_classical", [60, 66, 72, 78]),
    ("电影管弦配乐", "tag_a_cinematic", [50, 60, 70, 80]),
    ("爵士 Standard", "tag_a_jazz", [90, 100, 110, 120]),
    ("古风国风", "tag_a_guofeng", [68, 72, 76, 80]),
    ("嘻哈说唱", "tag_a_hiphop", [85, 90, 95, 100]),
    ("氛围纯音乐 Ambient", "tag_a_ambient", [55, 60, 65, 70]),
    ("Tropical House", "tag_a_electronic", [118, 120, 122, 125]),
    ("后摇 Post-rock", "tag_a_rock", [60, 66, 72, 78]),
    ("Bossa Nova", "tag_a_jazz", [72, 76, 80, 84]),
    ("儿童歌曲", "tag_a_happy", [100, 108, 115, 120]),
    ("Reggaeton", "tag_a_hiphop", [92, 96, 100, 104]),
    ("新灵魂乐 Neo-soul", "tag_a_pop", [92, 96, 98, 102]),
    ("史诗合唱", "tag_a_epic", [60, 68, 76, 84]),
]
A_MOOD = [
    ("克制的遗憾与思念", "深夜雨后的伤感，情绪内敛不宣泄", "tag_a_sad"),
    ("阳光明亮的治愈感", "清新自然，温暖治愈", "tag_a_chill"),
    ("紧张推进的末日感", "压抑中带爆发，热血昂扬", "tag_a_epic"),
    ("慵懒放松的周末午后", "慢节奏，松弛惬意", "tag_a_chill"),
    ("怀旧忧郁的80年代", "复古合成器，淡淡的忧伤", "tag_a_sad"),
    (" celebration 庆祝的喜悦", "明亮温暖，充满力量", "tag_a_happy"),
    ("孤独深夜的沉思", "安静疏离，亲密反思", "tag_a_sad"),
    ("自由奔放的公路感", "开阔洒脱，冲动热烈", "tag_a_happy"),
    ("空灵梦幻的氛围", "飘渺柔美，梦境质感", "tag_a_ambient"),
    ("坚定昂扬的胜利感", "史诗壮阔，情绪层层递进", "tag_a_epic"),
]
A_INSTR = [
    "带混响的钢琴与环境雨声", "木吉他扫弦与轻快鼓组", "失真电吉他与厚重贝斯、密集鼓点",
    "模拟合成器琶音与门限混响鼓", "弦乐四重奏与大提琴独奏", "萨克斯与爵士鼓刷",
    "古筝与竹笛", "808低音鼓与踩镲", " Rhodes 电钢琴与柔和黑胶底噪",
    "全交响乐团：弦乐铺垫、铜管高潮、定音鼓", "马林巴琴与钢鼓", "尼龙弦古典吉他与沙锤",
]
A_VOCAL = [
    "女声贴近耳边，咬字清晰，气声", "男声低沉沙哑，叙述感", "合唱与和声堆叠",
    "无人声纯音乐 Instrumental", "童声清澈干净", "说唱 flows，节奏鲜明",
    "女高音空灵吟唱", "男女对唱，情感张力",
]
A_STRUCT = [
    "前奏留白铺垫，主歌低声叙述，副歌加入弦乐情绪上扬，桥段收弱，末段副歌加和声",
    "前奏简短抓耳，主歌讲故事，副歌旋律易记且重复，尾奏渐弱",
    "开头低频铺垫，主歌断裂鼓点，副歌爆发失真吉他，中段静音后回到更强终章",
    "Intro 氛围引入，Verse 叙事，Pre-Chorus 推情绪，Chorus 高潮，Bridge 视角转换，Outro 渐弱",
    "全程氛围铺底，无明显段落，缓慢起伏，适合背景",
]


def fence_for(content: str) -> str:
    longest = max((len(m.group(0)) for m in re.finditer(r"~+", content)), default=0)
    return "~" * max(5, longest + 3)


def render_tpl(t: dict) -> str:
    fence = fence_for(t["content"])
    return "\n".join([
        f'<!-- CBTPL id="{t["id"]}" -->',
        "title: " + json.dumps(t["title"], ensure_ascii=False),
        "tags: " + json.dumps(t["tags"], ensure_ascii=False),
        "created: " + json.dumps(t["created"], ensure_ascii=False),
        "updated: " + json.dumps(t["updated"], ensure_ascii=False),
        fence,
        t["content"],
        fence,
        f'<!-- CBTPL-END id="{t["id"]}" -->',
        "",
    ])


def make_tpl(cat, idx, title, content, tags):
    return {
        "id": f"gen_{cat}_{idx:04d}",
        "title": title,
        "content": content,
        "category": cat,
        "tags": list(dict.fromkeys(tags)),
        "created": NOW,
        "updated": NOW,
    }


def gen_video():
    out = []
    mode_map = [
        ("文生视频", "tag_v_t2v", "无参考素材，纯文字生成镜头"),
        ("首尾帧生视频", "tag_v_fl2v", "首帧必接，尾帧可选，模型补全中间过程"),
        ("参考生视频", "tag_v_ref2v", "参考图/视频锁定人设与运镜"),
        ("故事板生视频", "tag_v_board", "按四/六/九宫格逐格生成镜头"),
    ]
    idx = 0
    for i in range(350):
        scene = V_SCENES[i % len(V_SCENES)]
        lens = V_LENS[(i * 3) % len(V_LENS)]
        light = V_LIGHT[(i * 5) % len(V_LIGHT)]
        sound = V_SOUND[(i * 7) % len(V_SOUND)]
        style = V_STYLE[(i * 2) % len(V_STYLE)]
        dur = V_DUR[i % len(V_DUR)]
        mode, mode_tag, mode_note = mode_map[i % len(mode_map)]
        ratio = "16:9" if i % 3 else "9:16"
        title = f"{mode}·{style.split('，')[0]}·{scene[:10]}"
        content = (
            f"{dur}秒，{ratio}，{style}。\n"
            f"画面：{scene}。\n"
            f"镜头：{lens}。\n"
            f"光线：{light}。\n"
            f"声音：{sound}。\n"
            f"模式：{mode}（{mode_note}）。\n"
            f"要求：单一连续镜头，主体不变形，无闪烁无伪影，动作连贯自然，结尾构图完整。"
        )
        tags = ["tag_scene", mode_tag, "tag_v_camera", "tag_v_cine"]
        # 追加风格对应标签
        for key, t in [("赛博", "tag_v_urban"), ("古风", "tag_v_cine"), ("动漫", "tag_v_anime"),
                       ("3D", "tag_v_3d"), ("像素", "tag_v_pixel"), ("黑白", "tag_v_noir"),
                       ("水墨", "tag_v_3d")]:
            if key in style:
                tags.append(t)
        if "雨" in scene or "霓虹" in scene:
            tags.append("tag_v_urban")
        if "日出" in scene or "晨" in scene or "海" in scene or "雪" in scene or "沙漠" in scene:
            tags.append("tag_v_nature")
        if "奔跑" in scene or "挥剑" in scene or "举重" in scene:
            tags.append("tag_v_action")
        if "女孩" in scene or "少女" in scene or "女子" in scene:
            tags.append("tag_role")
        if "车" in scene or "咖啡" in scene:
            tags.append("tag_prop")
        idx += 1
        out.append(make_tpl("video", idx, title, content, tags))
    return out


def gen_image():
    out = []
    idx = 0
    for i in range(350):
        subject = I_SUBJECTS[i % len(I_SUBJECTS)]
        style_name, style_text, style_tag = I_STYLES[i % len(I_STYLES)]
        light = I_LIGHT[(i * 3) % len(I_LIGHT)]
        ratio = ["1:1", "16:9", "9:16", "4:3"][i % 4]
        title = f"{style_name}·{subject[:12]}"
        content = (
            f"{subject}，{style_text}。\n"
            f"光线：{light}。画幅：{ratio}。\n"
            f"细节丰富，构图讲究，主体突出，高清质感。\n"
            f"负面：低质量，模糊，水印，文字，畸形手部，多余手指，过度曝光。"
        )
        tags = ["tag_scene", style_tag]
        if any(k in subject for k in ("猫", "狐狸", "熊猫", "独角兽", "小狗", "水母")):
            tags.append("tag_i_animal")
        if any(k in subject for k in ("女孩", "女子", "仙女", "宇航员", "武士", "模特")):
            tags.append("tag_role")
        if any(k in subject for k in ("咖啡", "收音机", "面包")):
            tags.append("tag_i_food" if "面包" in subject or "咖啡" in subject else "tag_prop")
        if "美食" in style_name:
            tags.append("tag_i_food")
        idx += 1
        out.append(make_tpl("image", idx, title, content, tags))
    return out


def gen_audio():
    out = []
    idx = 0
    for i in range(300):
        genre, genre_tag, bpms = A_GENRES[i % len(A_GENRES)]
        mood, mood_text, mood_tag = A_MOOD[(i * 3) % len(A_MOOD)]
        instr = A_INSTR[(i * 5) % len(A_INSTR)]
        vocal = A_VOCAL[(i * 7) % len(A_VOCAL)]
        struct = A_STRUCT[(i * 2) % len(A_STRUCT)]
        bpm = bpms[i % len(bpms)]
        model = "MiniMax Music" if i % 2 == 0 else "天工 Yue2"
        model_tag = "tag_a_minimax" if i % 2 == 0 else "tag_a_yue2"
        title = f"{genre}·{mood.split('的')[-1][:6]}·{bpm}BPM"
        content = (
            f"曲风：{genre}，{bpm} BPM。\n"
            f"情绪：{mood}，{mood_text}。\n"
            f"编曲：{instr}。\n"
            f"人声：{vocal}。\n"
            f"段落：{struct}。\n"
            f"制作：{model} 风格，混音干净，层次分明，适合短视频/叙事配乐。"
        )
        tags = [genre_tag, mood_tag, model_tag]
        tags.append("tag_a_inst" if "无人声" in vocal else "tag_a_vocal")
        if "古风" in genre or "国风" in genre:
            tags.append("tag_a_guofeng")
        if "电影" in genre or "史诗" in genre:
            tags.append("tag_a_cinematic")
        idx += 1
        out.append(make_tpl("audio", idx, title, content, tags))
    return out


def save_tags():
    lines = ["# 标签库", "", "> ComfyBridge 提示词模板库的标签。一个标签一个色，颜色不重复。", ""]
    for tid, name, color in TAGS:
        lines += [f'<!-- CBTAG id="{tid}" color="{color}" -->', name, "<!-- CBTAG-END -->", ""]
    (BASE / "标签.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def save_cat(cat, fname, cn, items):
    lines = [f"# {cn} · 提示词模板", "",
             "> ComfyBridge 提示词模板库自动维护。请尽量在页面里编辑，手改后刷新页面即可生效。", ""]
    for t in items:
        lines.append(render_tpl(t))
    (BASE / fname).write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    BASE.mkdir(parents=True, exist_ok=True)
    save_tags()
    vids, imgs, auds = gen_video(), gen_image(), gen_audio()
    save_cat("video", "视频.md", "视频", vids)
    save_cat("image", "图片.md", "图片", imgs)
    save_cat("audio", "音频.md", "音频", auds)
    total = len(vids) + len(imgs) + len(auds)
    print(f"生成完成：视频 {len(vids)} + 图片 {len(imgs)} + 音频 {len(auds)} = {total}，标签 {len(TAGS)}")


if __name__ == "__main__":
    main()
