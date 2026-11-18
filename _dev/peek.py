from PIL import Image
from pathlib import Path

for n in ["u_cap_list", "u_admin_menu", "u_tpl_scene", "u_tpl_prop", "u_admin_grades_scene",
          "u_admin_grades_prop", "u_admin_grades_board", "u_composer_scene", "u_composer_prop",
          "u_composer_board", "u_scene_preview", "u_board_preview", "u_jobcard_scene",
          "u_jobcard_board", "u_jobcard_prop", "u_gallery", "u_admin_caps", "u_phone_scene",
          "u_phone_board", "u_m_nav", "u_m_right", "u_m_admin_nav", "u_studio_overview",
          "u_studio_scene", "u_studio_scene_f9", "u_studio_board", "u_studio_board_preview",
          "u_composer_scene_expand", "u_composer_prop_sv", "u_composer_board_g6"]:
    p = Path("../_reference/docs103") / f"{n}.png"
    if p.exists():
        print(n, Image.open(p).size)
    else:
        print(n, "MISSING")
