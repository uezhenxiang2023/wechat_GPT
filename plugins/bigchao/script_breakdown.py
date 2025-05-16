"""
BigChao API
@author uezhenxiang2023
@Date 2025/05/13
"""

import os
import re
import json

import docx
import pandas as pd
import matplotlib.pyplot as plt
from pypdf import PdfReader
from google.genai.types import Part

from config import conf
from common import memory
from common.log import logger
from common.tmp_dir import TmpDir

model = conf().get('model').upper()


def screenplay_scenes_breakdown(
        fn_name,
        *,
        screenplay_title: str = None,
        total_pages: str = None,
        total_words: str = None,
        scenes_list: list = []
    ):
    """scenes breakdown for screenscript"""
    screenplay_title_no_quotes = screenplay_title.replace('《', '').replace('》', '')
    # 遍历./tmp目录下的文件,如果文件名中含有screenplay_title,则将其路径赋值给path
    for root, dirs, files in os.walk('./tmp'):
        for file in files:
            if screenplay_title_no_quotes in file and file.endswith(('.docx', '.pdf')):
                path = os.path.join(root, file)
                break

    if path.endswith('.docx'):
        reader = docx.Document(path)
        texts = ''
        line_list = []
        num_id = 1
        # 遍历文档中的段落
        for paragraph in reader.paragraphs:
            # 提取文本内容
            scene_normal = paragraph.text.strip()
            # 检查段落是否有序号
            if paragraph._p.pPr and paragraph._p.pPr.numPr:
                scene_normal = f"{num_id}. " + scene_normal
                num_id += 1
            texts = texts + scene_normal + '\n'
            line_list.append(scene_normal)
        total_pages = len(texts)//500 + 1
    elif path.endswith('.pdf'):
        reader = PdfReader(path)
        total_pages = len(reader.pages)
        texts = ''.join([page.extract_text() for page in reader.pages])
        # 删除脚标
        footer_pattern = rf"\[{re.escape(screenplay_title_no_quotes)}\]\s*◁[\s\x00-\x1f]*▷\s*\n?"
        texts = re.sub(footer_pattern, "", texts, flags=re.DOTALL)
        line_list = re.split(r'[。！？\n]|\s{2,}', texts)
    total_words = len(texts)
    words_per_page = total_words / total_pages
    # 统计每一场的字数
    paragraph = ""
    sc_count = 0
    counter_dict ={}
    # 定义场次描述规则
    pattern = r"第.*场|场景.*|\d+\..*"
    for i, v in enumerate(line_list):
        # 只要每行的前3～7个字，符合场次描述规则
        if any(re.match(pattern, v.strip()[:n]) is not None for n in (2, 3, 4, 5, 6, 7, 9, 10)):
            counter_dict[f"scene{sc_count}"] = f'{len(paragraph)}'
            sc_count += 1
            paragraph = ""
        paragraph += v.strip()
    # 循环结束后，捕获最后一场戏的字数
    counter_dict[f"scene{sc_count}"] = f'{len(paragraph)}'
    del counter_dict["scene0"]

    for i, v in enumerate(scenes_list):
        scene_id = v['scene_id']
        try:
            words_per_scene = int(counter_dict.get(f"scene{scene_id}"))
        except Exception as e:
            logger.error(f"[TELEGRAMBOT_{model}] scene_id={scene_id} not found in counter_dict, error={e}")
            continue
        pages_per_scene = round(words_per_scene / words_per_page, 2)
        estimated_duration = round(pages_per_scene * 1.2, 2)
        v.update(words = words_per_scene)
        v.update(pages = pages_per_scene)
        v.update(estimated_duration = estimated_duration)

    # Save the scenes list to an Excel file
    scenes_list_str = json.dumps(scenes_list, ensure_ascii=False)
    df_scenses_list = pd.read_json(scenes_list_str)
    new_cols = ['scene_id', 'location', 'daynight', 'envirement', 'words', 'pages', 'estimated_duration', 'assets_id']
    df_scenses_list = df_scenses_list.reindex(columns=new_cols)
    file_path = TmpDir().path()+ f"{screenplay_title}_scenes_breakdown.xlsx"
    df_scenses_list.to_excel(
        file_path,
        sheet_name=f'{screenplay_title}_scenes_breakdown',
        index=False
    )
    logger.info(f"[TELEGRAMBOT_{model}] {file_path} is saved")
    api_response = {
        'total_pages': total_pages,
        'total_words': total_words,
        'scenes_list': scenes_list
    }
    # Create a function response part
    function_response_part = []
    function_response_obj = Part.from_function_response(
        name=fn_name,
        response=api_response,
    )
    function_response_part.append(function_response_obj)
    # Create a function response text part
    function_response_comment = "这是函数返回的原始场景表，主要用来统计页数和时长，不用针对该表内容进行回复。"
    function_response_text = Part.from_text(
        text=function_response_comment
    )
    function_response_part.append(function_response_text)
    return function_response_part


def screenplay_assets_breakdown(
        fn_name,
        *,
        screenplay_title: str = None,
        assets_list: list = []
    ):
    """assets breaddown for screenscript"""
    for i, v in enumerate(assets_list):
        ref_id = i+1
        v.update(ref_url = f'RefURL_{ref_id}')

    assets_list_str = json.dumps(assets_list, ensure_ascii=False)
    df_assets_list = pd.read_json(assets_list_str)
    df_scenes_list = pd.read_excel(f'./tmp/{screenplay_title}_scenes_breakdown.xlsx')
    for i, v in enumerate(df_assets_list['scene_ids']):
        asset_pages = 0
        for n in v:
            # 从scenes_list中取出对应的scene_id所在的行
            scene_row = df_scenes_list[df_scenes_list['scene_id'] == n]
            # 取出该行asset_pages列的值
            asset_page = scene_row['pages'].values[0]
            asset_pages += asset_page
        # 将assets_pages和estimated_asset_duration更新到assets_list中
        df_assets_list.at[i, 'asset_pages'] = asset_pages
        df_assets_list.at[i, 'estimated_asset_duration'] = round(asset_pages * 1.2, 2)

    # Save the assets list to an Excel file
    new_cols = ['asset_type', 'asset_id', 'name', "visual_effects_type", 'visual_effects_description', 'scene_ids', 'asset_pages', 'estimated_asset_duration', 'ref_url']
    df_assets_list = df_assets_list.reindex(columns=new_cols)
    assets_list = df_assets_list.to_dict('records')
    assets_breakdown_file_path = TmpDir().path()+ f"{screenplay_title}_assets_breakdown.xlsx"
    df_assets_list.to_excel(
        assets_breakdown_file_path,
        sheet_name=f'{screenplay_title}_assets_breakdown',
        index=False
    )
    logger.info(f"[TELEGRAMBOT_{model}] {assets_breakdown_file_path} is saved")

    # 统计场景引用的资产,更新scenes_list
    for i, v in enumerate(df_scenes_list['scene_id']):
        assets_id = []
        for asset_id_index, scene_ids in enumerate(df_assets_list['scene_ids']):
            asset_scene_ids = [int(x) for x in scene_ids]
            if v in asset_scene_ids:
                assets_id.append(df_assets_list['asset_id'][asset_id_index])
        # 将assets_id添加到scenes_list中，转换为字符串形式
        df_scenes_list.at[i, 'assets_id'] = str(assets_id)
    scenes_list = df_scenes_list.to_dict('records')
    scenes_breakdown_file_path = TmpDir().path()+ f"{screenplay_title}_scenes_breakdown.xlsx"
    df_scenes_list.to_excel(
        scenes_breakdown_file_path,
        sheet_name=f'{screenplay_title}_scenes_breakdown',
        index=False
    )
    logger.info(f"[TELEGRAMBOT_{model}] {scenes_breakdown_file_path} is updated")

    """# Send the scenes_breakdown.xlsx to the user
    with open(scenes_breakdown_file_path, 'rb') as f:
        TelegramChannel().send_file(f, context["receiver"])
    logger.info("[TELEGRAMBOT_{}] sendMsg={}, receiver={}".format(self.Model_ID, scenes_breakdown_file_path, context["receiver"]))"""

    """# Create visualization figure of the assets list
    plt.switch_backend('agg') # Use a non-interactive backend
    plt.rcParams['font.sans-serif'] = ['SimHei'] # Set Chinese font
    plt.rcParams['axes.unicode_minus'] = False # Fix the minus sign display issue
    asset_types = set([asset['asset_type'] for asset in assets_list])
    colormap = {
        'cast': None,
        'extra_silent': 'darkcyan',
        'extra_atmosphere': 'darkblue',
        'stunts': 'darkgrey',
        'costume': None,
        'makeup': None,
        'location': 'darkgreen',
        'set_dressing': 'darkred',
        'vehicle': 'darkgrey',
        'animal': 'darkgoldenrod',
        'livestock': None,
        'prop': 'darkorange',
        'special_effects': None
    }
    for asset_type in asset_types:
        figsize = (30, 5)
        plt.figure(figsize=figsize)
        subset_assets_list = df_assets_list[df_assets_list['asset_type'] == asset_type]
        ax = subset_assets_list.plot(
            figsize=figsize,
            title=f'{screenplay_title}_Assets_Breakdown-{asset_type.upper()}',  
            kind='bar', 
            x='name', 
            y='estimated_asset_duration', 
            ylabel='资产预计时长(分钟)', 
            color=colormap[asset_type],
            legend=False
        )
        # Add value labels on top of the bars
        for i, v in enumerate(subset_assets_list['estimated_asset_duration']):
            ax.text(i, v, f'{v:.2f}', ha='center', va='bottom', fontsize=10)

        figure_path = TmpDir().path() + f"{screenplay_title}_assets_breakdown-{asset_type.upper()}.png"
        plt.savefig(figure_path, dpi=200, bbox_inches='tight')
        plt.close()
        logger.info(f"[TELEGRAMBOT_{self.Model_ID}] {figure_path} is saved")

        # Send the visualization figure to the user
        with open(figure_path, 'rb') as f:
            f.seek(0)
            TelegramChannel().send_image(f, context["receiver"])
        logger.info("[TELEGRAMBOT_{}] sendMsg={}, receiver={}".format(self.Model_ID, figure_path, context["receiver"]))"""

    api_response = {
        'scenes_list': json.dumps(scenes_list),
        'assets_list': json.dumps(assets_list),
        'file_pathes': [scenes_breakdown_file_path, assets_breakdown_file_path]
    }
    # Create a function response part
    function_response_part = []
    function_response_obj = Part.from_function_response(
        name=fn_name,
        response=api_response,
    )
    function_response_part.append(function_response_obj)
    # Create a function response text part
    function_response_comment = "这是函数返回的最终场景表和资产表，回复时不用包含表中详细内容，总结后简单回复即可。"
    function_response_text = Part.from_text(
        text=function_response_comment
    )
    function_response_part.append(function_response_text)
    return function_response_part


def cache_media(media_path, media_file, context):
        session_id = context["session_id"]
        if session_id not in memory.USER_IMAGE_CACHE:
            memory.USER_IMAGE_CACHE[session_id] = {
                "path": [media_path],
                "files": [media_file]
            }
        else:
            memory.USER_IMAGE_CACHE[session_id]["path"].append(media_path)
            memory.USER_IMAGE_CACHE[session_id]["files"].append(media_file)
        logger.info(f"[{model}] {media_path} cached to memory")
        return None
