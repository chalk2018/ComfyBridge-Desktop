"""生成 1.04 新增的视频 / 音频工作流（ComfyUI API 格式）。

为什不直接转换 UI 工作流：这几条工作流要么是 subgraph 包装（音频），要么带着本机
没装的自定义节点（TESpeed / CR Prompt Text），转换产物在 /prompt 校验里会缺字段。
这里按「只留真正要跑的节点」写死一份干净图，注入点固定、字段可控：

- 视频：MiniMax H3（fl2va / ref2va），LoRA + 步数用一个开关切两档
  （4/8 步走加速 LoRA，20 步走原模型），参考图/参考视频/参考音频都由 videolib 注入；
- 音乐：MiniMax Music 3（文生音）、YuE2 cover（参考音频复刻）；
- 语音 / 克隆：AICG3D SparkTTS。

用法：
    .\\.venv\\Scripts\\python.exe tools\\build_video_audio_wf.py
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "workflows"

CLIP = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
VAE_VIDEO = "minimax_h3_video_vae_int8_convrot.safetensors"
VAE_AUDIO = "minimax_h3_audio_vae_fp32.safetensors"

UNET_FL2V = "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
UNET_R2V = "minimax_h3_ref2va_pruned_int8_convrot.safetensors"
LORA_FL4 = "minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors"
LORA_FL8 = "minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors"
LORA_R4 = "minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors"
LORA_R8 = "minimax_h3_ref2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors"


def video_prompt(unet: str, lora4: str, lora8: str) -> dict:
    """H3 一条完整出片链：reference-to-video 节点 + 可选 LoRA + 视频/音频解码。"""
    return {
        "1": {"class_type": "UNETLoader",
              "inputs": {"unet_name": unet, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": CLIP, "type": "minimax", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": VAE_VIDEO}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": VAE_AUDIO}},
        # 10 号是唯一要注入的节点：提示词 / 宽高 / 帧数 / 参考槽
        "10": {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": {
            "clip": ["2", 0], "vae": ["3", 0], "audio_vae": ["4", 0],
            "prompt": ["11", 0], "width": 1344, "height": 768,
            "length": 124, "ref_image_size": "match",
        }},
        "11": {"class_type": "PrimitiveStringMultiline", "inputs": {"value": ""}},
        # 开关：True = 加速 LoRA + 4/8 步；False = 原模型 + 20 步
        "12": {"class_type": "ComfySwitchNode", "inputs": {
            "switch": ["15", 0], "on_false": ["1", 0], "on_true": ["14", 0]}},
        "13": {"class_type": "ComfySwitchNode", "inputs": {
            "switch": ["15", 0], "on_false": ["16", 0], "on_true": ["17", 0]}},
        # 14 号在 4 步 / 8 步两个 LoRA 之间选，再由 12 号决定用不用它
        "14": {"class_type": "ComfySwitchNode", "inputs": {
            "switch": ["27", 0], "on_false": ["18", 0], "on_true": ["19", 0]}},
        "18": {"class_type": "LoraLoaderModelOnly", "inputs": {
            "model": ["1", 0], "lora_name": lora4, "strength_model": 1.0}},
        "19": {"class_type": "LoraLoaderModelOnly", "inputs": {
            "model": ["1", 0], "lora_name": lora8, "strength_model": 1.0}},
        "27": {"class_type": "PrimitiveBoolean", "inputs": {"value": False}},
        "15": {"class_type": "PrimitiveBoolean", "inputs": {"value": True}},
        "16": {"class_type": "PrimitiveInt", "inputs": {"value": 20}},
        "17": {"class_type": "PrimitiveInt", "inputs": {"value": 4}},
        "20": {"class_type": "BasicScheduler", "inputs": {
            "model": ["12", 0], "scheduler": "simple", "steps": ["13", 0], "denoise": 1}},
        "21": {"class_type": "BasicGuider", "inputs": {"model": ["12", 0], "conditioning": ["10", 0]}},
        "22": {"class_type": "RandomNoise", "inputs": {"noise_seed": 0}},
        "23": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "res_multistep"}},
        "24": {"class_type": "SamplerCustomAdvanced", "inputs": {
            "noise": ["22", 0], "guider": ["21", 0], "sampler": ["23", 0],
            "sigmas": ["20", 0], "latent_image": ["10", 1]}},
        "25": {"class_type": "VAEDecode", "inputs": {"samples": ["24", 0], "vae": ["3", 0]}},
        "26": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["24", 0], "vae": ["4", 0]}},
        "30": {"class_type": "CreateVideo", "inputs": {
            "images": ["25", 0], "audio": ["26", 0], "fps": 24,
            "bit_depth": 8, "color_space": "sRGB", "codec": "none"}},
        "31": {"class_type": "SaveVideo", "inputs": {
            "video": ["30", 0], "filename_prefix": "ComfyBridge/video",
            "format": "auto", "format.codec": "auto", "codec": "auto"}},
    }


def video_bridge(lora4: str, lora8: str) -> dict:
    return {
        "prompt_targets": [{"node": "11", "field": "value"}],
        "seed_targets": [{"node": "22", "field": "noise_seed"}],
        "ref_slots": [],
        "output_nodes": ["31"],
        "root_load_images": [],
        "media": "video",
        # videolib 用这几行做注入，别在代码里再写死节点号
        "video_ref": {
            "node": "10", "prompt": "11", "width": "width", "height": "height",
            "length": "length", "switch": "15", "steps": "17", "base_steps": "16",
            "lora_switch": "14", "use8": "27", "lora4": lora4, "lora8": lora8,
        },
        "class_types": {},
    }


def music_prompt() -> dict:
    """MiniMax Music 3：一句话风格 + 歌词 → 带人声的成品音乐。"""
    return {
        "1": {"class_type": "UNETLoader",
              "inputs": {"unet_name": "minimax_music3_dit_fp16.safetensors", "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {
            "clip_name": "minimax_music3_text_encoder_pruned_int8_convrot.safetensors",
            "type": "minimax", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "minimax_music3_dav.safetensors"}},
        "4": {"class_type": "MiniMaxMusic3TextEncode", "inputs": {
            "clip": ["2", 0], "caption": "", "lyrics": "", "seed": 0,
            "max_duration": 60.0, "cfg_scale": 1.7, "top_k": 50}},
        "5": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["4", 0]}},
        "6": {"class_type": "EmptyMiniMaxMusic3LatentAudio", "inputs": {
            "seconds": ["4", 1], "batch_size": 1}},
        "7": {"class_type": "KSampler", "inputs": {
            "model": ["1", 0], "positive": ["4", 0], "negative": ["5", 0],
            "latent_image": ["6", 0], "seed": 0, "steps": 30, "cfg": 1.7,
            "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
        "8": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["7", 0], "vae": ["3", 0]}},
        "9": {"class_type": "SaveAudioAdvanced", "inputs": {
            "audio": ["8", 0], "filename_prefix": "ComfyBridge/music",
            "format": "mp3", "format.quality": "V0"}},
    }


def music_bridge() -> dict:
    return {
        "prompt_targets": [{"node": "4", "field": "caption"}],
        "seed_targets": [
            {"node": "4", "field": "seed"},
            {"node": "7", "field": "seed"},
        ],
        "ref_slots": [],
        "output_nodes": ["9"],
        "root_load_images": [],
        "media": "audio",
        "lyrics_target": {"node": "4", "field": "lyrics"},
        "duration_target": {"node": "4", "field": "max_duration"},
        "class_types": {},
    }


def music_cover_prompt() -> dict:
    """YuE2 cover：给一段参考音频，按它的人声/旋律线重做一首新歌。"""
    return {
        "1": {"class_type": "CheckpointLoaderSimple",
              "inputs": {"ckpt_name": "yue2_3b_int8_convrot.safetensors"}},
        "2": {"class_type": "AudioEncoderLoader",
              "inputs": {"audio_encoder_name": "sheetsage2_bf16.safetensors"}},
        "3": {"class_type": "LoadAudio", "inputs": {"audio": "_cb_blank.wav"}},
        "4": {"class_type": "SheetSage2AudioToABC", "inputs": {
            "audio_encoder": ["2", 0], "audio": ["3", 0], "mode": "melody"}},
        "5": {"class_type": "YuE2GenerateMusic", "inputs": {
            "clip": ["1", 1], "style": "", "lyrics": "", "abc": ["4", 0],
            "seed": 0, "mode": "melody", "max_duration": 120.0,
            "temperature": 1.0, "top_p": 0.95, "top_k": 100, "repetition_penalty": 1.2}},
        "6": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["5", 0]}},
        "7": {"class_type": "EmptyYuE2LatentAudio", "inputs": {"seconds": ["5", 1], "batch_size": 1}},
        "8": {"class_type": "KSampler", "inputs": {
            "model": ["1", 0], "positive": ["5", 0], "negative": ["6", 0],
            "latent_image": ["7", 0], "seed": 0, "steps": 32, "cfg": 1.0,
            "sampler_name": "dpm_2", "scheduler": "sgm_uniform", "denoise": 1.0}},
        "9": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["8", 0], "vae": ["1", 2]}},
        "10": {"class_type": "SaveAudioAdvanced", "inputs": {
            "audio": ["9", 0], "filename_prefix": "ComfyBridge/musiccover",
            "format": "mp3", "format.quality": "V0"}},
    }


def music_cover_bridge() -> dict:
    return {
        "prompt_targets": [{"node": "5", "field": "style"}],
        "lyrics_target": {"node": "5", "field": "lyrics"},
        "seed_targets": [{"node": "5", "field": "seed"}, {"node": "8", "field": "seed"}],
        "ref_slots": [],
        "output_nodes": ["10"],
        "root_load_images": [],
        "media": "audio",
        "duration_target": {"node": "5", "field": "max_duration"},
        "audio_ref": {"load": "3", "field": "audio"},
        "class_types": {},
    }


def tts_prompt() -> dict:
    return {
        "1": {"class_type": "SparkTTSRun", "inputs": {
            "text": "", "gender": "female", "age": "Youth-Adult",
            "pitch": "moderate", "speed": "moderate", "temperature": 0.8,
            "top_k": 50, "top_p": 0.95, "max_new_tokens": 3000,
            "do_sample": True, "unload_model": True, "seed": 0}},
        "2": {"class_type": "SaveAudioAdvanced", "inputs": {
            "audio": ["1", 0], "filename_prefix": "ComfyBridge/tts",
            "format": "mp3", "format.quality": "V0"}},
    }


def tts_bridge() -> dict:
    return {
        "prompt_targets": [{"node": "1", "field": "text"}],
        "seed_targets": [{"node": "1", "field": "seed"}],
        "ref_slots": [],
        "output_nodes": ["2"],
        "root_load_images": [],
        "media": "audio",
        "class_types": {},
    }


def clone_prompt() -> dict:
    return {
        "1": {"class_type": "LoadAudio", "inputs": {"audio": "_cb_blank.wav"}},
        "2": {"class_type": "SparkTTSClone", "inputs": {
            "text": "", "cloned_speaker": "婷婷", "temperature": 0.8,
            "top_k": 50, "top_p": 0.95, "max_new_tokens": 3000,
            "do_sample": True, "unload_model": True, "seed": 0,
            "custom_clone_audio": ["1", 0]}},
        "3": {"class_type": "SaveAudioAdvanced", "inputs": {
            "audio": ["2", 0], "filename_prefix": "ComfyBridge/clone",
            "format": "mp3", "format.quality": "V0"}},
    }


def clone_bridge() -> dict:
    return {
        "prompt_targets": [{"node": "2", "field": "text"}],
        "seed_targets": [{"node": "2", "field": "seed"}],
        "ref_slots": [],
        "output_nodes": ["3"],
        "root_load_images": [],
        "media": "audio",
        "audio_ref": {"load": "1", "field": "audio"},
        "class_types": {},
    }


FILES = {
    "视频生成-文生视频-h3": (video_prompt(UNET_FL2V, LORA_FL4, LORA_FL8), video_bridge(LORA_FL4, LORA_FL8)),
    "视频生成-首尾帧生视频-h3": (video_prompt(UNET_FL2V, LORA_FL4, LORA_FL8), video_bridge(LORA_FL4, LORA_FL8)),
    "视频生成-参考生视频-h3": (video_prompt(UNET_R2V, LORA_R4, LORA_R8), video_bridge(LORA_R4, LORA_R8)),
    "视频生成-故事板生视频-h3": (video_prompt(UNET_R2V, LORA_R4, LORA_R8), video_bridge(LORA_R4, LORA_R8)),
    "音频-音乐生成": (music_prompt(), music_bridge()),
    "音频-参考生成音乐": (music_cover_prompt(), music_cover_bridge()),
    "音频-语音生成": (tts_prompt(), tts_bridge()),
    "音频-声音克隆": (clone_prompt(), clone_bridge()),
}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, (api, bridge) in FILES.items():
        (OUT / f"{name}.json").write_text(
            json.dumps(api, ensure_ascii=False, indent=1), encoding="utf-8")
        (OUT / f"{name}.bridge.json").write_text(
            json.dumps(bridge, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"写出 {name}.json（{len(api)} 个节点）")


if __name__ == "__main__":
    main()
