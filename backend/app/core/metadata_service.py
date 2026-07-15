import os
import re
import asyncio
from datetime import datetime, timedelta
from typing import Optional, Dict, List
import requests
import logging
import json

from ..config.settings import get_config
from ..models.database import WorkMetadata as WorkMetadataModel, get_db
from ..core.task_engine import Task

logger = logging.getLogger(__name__)

class WorkMetadata:
    """作品元数据"""
    def __init__(self):
        self.rjcode: str = ""
        self.work_name: str = ""
        self.maker_id: str = ""
        self.maker_name: str = ""
        self.release_date: str = ""
        self.series_name: Optional[str] = None
        self.series_id: Optional[str] = None
        self.age_category: str = ""
        self.tags: list = []
        self.cvs: list = []
        self.cover_url: str = ""
    
    def to_dict(self) -> dict:
        return {
            'rjcode': self.rjcode,
            'work_name': self.work_name,
            'maker_id': self.maker_id,
            'maker_name': self.maker_name,
            'release_date': self.release_date,
            'series_name': self.series_name,
            'series_id': self.series_id,
            'age_category': self.age_category,
            'tags': self.tags,
            'cvs': self.cvs,
            'cover_url': self.cover_url
        }

class MetadataService:
    """元数据服务"""
    
    def __init__(self):
        self.config = get_config()
        self.session = requests.Session()
        if self.config.metadata.http_proxy:
            self.session.proxies = {
                'http': self.config.metadata.http_proxy,
                'https': self.config.metadata.http_proxy
            }
    
    async def fetch(self, path: str, task: Task) -> dict:
        """
        从路径中提取RJ号并获取元数据
        """
        # 从路径中提取RJ号
        rjcode = self._extract_rjcode(path)
        if not rjcode:
            raise Exception(f"无法从路径中提取RJ号: {path}")

        task.update_progress(65, f"获取元数据: {rjcode}")

        # 检查缓存
        if self.config.metadata.cache_enabled:
            cached = self._get_cached_metadata(rjcode)
            if cached:
                logger.info(f"使用缓存的元数据: {rjcode}")
                return cached.to_dict()

        # 从DLsite获取，失败时依次尝试 asmr.one → voicehub.top 作为备用
        try:
            metadata = await self._fetch_from_dlsite(rjcode)
        except Exception as e:
            logger.warning(f"[{rjcode}] DLsite 获取元数据失败: {e}，尝试 asmr.one 备用源")
            metadata = await self._fetch_from_asmr_one(rjcode)
            if metadata is None:
                logger.warning(f"[{rjcode}] asmr.one 获取失败，尝试 voicehub.top 备用源")
                metadata = await self._fetch_from_voicehub(rjcode)
                if metadata is None:
                    raise Exception(f"从 DLsite、asmr.one 和 voicehub.top 均未找到作品: {rjcode}")

        # 缓存到数据库
        if self.config.metadata.cache_enabled:
            self._cache_metadata(metadata)

        return metadata.to_dict()
    
    def _extract_rjcode(self, path: str) -> Optional[str]:
        """从路径中提取RJ号
        
        支持格式：
        - RJ123456, RJ12345678
        - VJ123456, BJ123456
        - 纯数字目录名：01503161 -> RJ01503161
        - 带前缀的数字：39.RJ01570159 -> RJ01570159
        """
        # 优先匹配标准格式 [RVB]J + 6/8位数字
        pattern = r'[RVB]J(\d{6}|\d{8})(?!\d)'
        match = re.search(pattern, path, re.IGNORECASE)
        if match:
            return match.group(0).upper()
        
        # 尝试从路径最后的目录/文件名中提取纯数字
        path_parts = re.split(r'[\\/]', path)
        if path_parts:
            last_part = path_parts[-1]
            # 移除常见前缀如 "39." 等
            clean_name = re.sub(r'^\d+\.', '', last_part)
            # 匹配6位或8位纯数字
            num_match = re.match(r'^(\d{6}|\d{8})$', clean_name)
            if num_match:
                num = num_match.group(1)
                return f"RJ{num}"
        
        return None
    
    def _get_cached_metadata(self, rjcode: str) -> Optional[WorkMetadataModel]:
        """从缓存获取元数据"""
        db = next(get_db())
        try:
            cached = db.query(WorkMetadataModel).filter(
                WorkMetadataModel.rjcode == rjcode
            ).first()
            
            if cached is not None and cached.expires_at > datetime.utcnow():
                return cached
            return None
        finally:
            db.close()
    
    def _cache_metadata(self, metadata: WorkMetadata):
        """缓存元数据到数据库"""
        db = next(get_db())
        try:
            # 删除旧缓存
            db.query(WorkMetadataModel).filter(
                WorkMetadataModel.rjcode == metadata.rjcode
            ).delete()
            
            # 创建新缓存
            cached = WorkMetadataModel(
                rjcode=metadata.rjcode,
                work_name=metadata.work_name,
                maker_id=metadata.maker_id,
                maker_name=metadata.maker_name,
                release_date=metadata.release_date,
                series_name=metadata.series_name,
                series_id=metadata.series_id,
                age_category=metadata.age_category,
                tags=metadata.tags,
                cvs=metadata.cvs,
                cover_url=metadata.cover_url,
                expires_at=datetime.utcnow() + timedelta(days=30)
            )
            db.add(cached)
            db.commit()
        except Exception as e:
            logger.error(f"缓存元数据失败: {e}")
            db.rollback()
        finally:
            db.close()
    
    async def _fetch_from_dlsite(self, rjcode: str) -> WorkMetadata:
        """从DLsite API获取元数据（支持大家翻译）"""
        await asyncio.sleep(self.config.metadata.sleep_interval)
        
        # 获取基础数据（使用配置的语言）
        url = f"https://www.dlsite.com/maniax/api/=/product.json?workno={rjcode}&locale={self.config.metadata.locale}"
        
        try:
            response = self.session.get(
                url,
                timeout=(self.config.metadata.connect_timeout, self.config.metadata.read_timeout)
            )
            response.raise_for_status()
            
            data = response.json()
            if not data or len(data) == 0:
                raise Exception(f"作品未找到: {rjcode}")
            
            product = data[0]
            metadata = WorkMetadata()
            metadata.rjcode = product.get('workno', rjcode)
            metadata.work_name = product.get('work_name', '')
            
            metadata.maker_id = product.get('maker_id', '')
            metadata.maker_name = product.get('maker_name', '')
            metadata.release_date = product.get('regist_date', '')[:10]
            metadata.series_name = product.get('series_name')
            metadata.series_id = product.get('series_id')
            metadata.cover_url = 'https:' + product.get('image_main', {}).get('url', '')
            
            # 年龄分级
            age_category = product.get('age_category', 3)
            if age_category == 1:
                metadata.age_category = 'GEN'
            elif age_category == 2:
                metadata.age_category = 'R15'
            else:
                metadata.age_category = 'ADL'
            
            # 标签
            for genre in product.get('genres', []):
                metadata.tags.append(genre.get('name', ''))
            
            # 声优
            creators = product.get('creaters', {})
            if isinstance(creators, dict) and 'voice_by' in creators:
                for cv in creators['voice_by']:
                    metadata.cvs.append(cv.get('name', ''))
            
            # 检查是否有大家翻译的中文标题
            translation_info = product.get('translation_info')
            if translation_info:
                logger.info(f"[{rjcode}] 发现翻译信息: {translation_info}")
                
                # 语言代码映射
                locale_map = {
                    'CHI_HANS': 'zh-CN',
                    'CHI_HANT': 'zh-TW',
                    'ENG': 'en-US',
                    'KOR': 'ko-KR',
                    'SPA': 'es-ES',
                    'DEU': 'de-DE',
                    'FRA': 'fr-FR',
                    'IND': 'id-ID',
                    'ITA': 'it-IT',
                    'POR': 'pt-PT',
                    'SWE': 'sv-SE',
                    'THA': 'th-TH',
                    'VIE': 'vi-VN'
                }
                
                translated_name = None
                
                # 情况1: 翻译作品（子作品）
                if not translation_info.get('is_original', True):
                    lang_code = translation_info.get('lang')
                    if lang_code:
                        try:
                            logger.info(f"[{rjcode}] 处理翻译作品，原语言: {lang_code}")
                            
                            # 优先尝试简体中文，然后是繁体中文，最后是作品本身的语言
                            tried_locales = []
                            
                            # 策略1: 如果原语言不是简体中文，先尝试简体中文
                            if lang_code != 'CHI_HANS':
                                logger.info(f"[{rjcode}] 尝试获取简体中文标题")
                                translated_name = await self._fetch_translated_title(rjcode, 'zh-CN', validate_chinese=True)
                                tried_locales.append('zh-CN')
                                if translated_name:
                                    logger.info(f"[{rjcode}] 成功获取简体中文翻译标题: {translated_name}")
                            
                            # 策略2: 如果简体中文失败且原语言不是繁体中文，尝试繁体中文
                            if not translated_name and lang_code != 'CHI_HANT':
                                logger.info(f"[{rjcode}] 简体中文不可用，尝试获取繁体中文标题")
                                translated_name = await self._fetch_translated_title(rjcode, 'zh-TW', validate_chinese=True)
                                tried_locales.append('zh-TW')
                                if translated_name:
                                    logger.info(f"[{rjcode}] 成功获取繁体中文翻译标题: {translated_name}")
                            
                            # 策略3: 使用作品本身的翻译语言
                            if not translated_name:
                                dlsite_locale = locale_map.get(lang_code, lang_code)
                                logger.info(f"[{rjcode}] 已尝试{tried_locales}，使用作品原locale {dlsite_locale}")
                                should_validate = lang_code in ['CHI_HANS', 'CHI_HANT']
                                translated_name = await self._fetch_translated_title(rjcode, str(dlsite_locale), validate_chinese=should_validate)
                                if translated_name:
                                    logger.info(f"[{rjcode}] 使用{lang_code}翻译标题: {translated_name}")
                        except Exception as e:
                            logger.warning(f"[{rjcode}] 获取翻译标题失败: {e}")
                
                # 情况2: 原作但有"大家来翻译"申请
                elif translation_info.get('is_translation_agree', False):
                    logger.info(f"[{rjcode}] 原作但有翻译申请，检查是否有可用的中文翻译")
                    
                    translation_status = translation_info.get('translation_status_for_translator', {})
                    logger.info(f"[{rjcode}] 翻译状态: {translation_status}")
                    
                    # 检查简体中文是否可用
                    chi_hans_status = translation_status.get('CHI_HANS', {})
                    if chi_hans_status.get('is_available', False) and not chi_hans_status.get('is_denied', True):
                        logger.info(f"[{rjcode}] 简体中文翻译申请可用，尝试获取")
                        try:
                            translated_name = await self._fetch_translated_title(rjcode, 'zh-CN', validate_chinese=True)
                            if translated_name:
                                logger.info(f"[{rjcode}] 成功获取简体中文翻译标题: {translated_name}")
                        except Exception as e:
                            logger.warning(f"[{rjcode}] 获取简体中文翻译标题失败: {e}")
                    
                    # 如果简体中文不可用或获取失败，尝试繁体中文
                    if not translated_name:
                        chi_hant_status = translation_status.get('CHI_HANT', {})
                        if chi_hant_status.get('is_available', False) and not chi_hant_status.get('is_denied', True):
                            logger.info(f"[{rjcode}] 繁体中文翻译申请可用，尝试获取")
                            try:
                                translated_name = await self._fetch_translated_title(rjcode, 'zh-TW', validate_chinese=True)
                                if translated_name:
                                    logger.info(f"[{rjcode}] 成功获取繁体中文翻译标题: {translated_name}")
                            except Exception as e:
                                logger.warning(f"[{rjcode}] 获取繁体中文翻译标题失败: {e}")
                
                if translated_name:
                    metadata.work_name = translated_name
            
            return metadata
            
        except requests.exceptions.RequestException as e:
            logger.error(f"请求DLsite失败: {e}")
            raise Exception(f"获取元数据失败: {e}")
    
    async def _fetch_from_asmr_one(self, rjcode: str) -> Optional[WorkMetadata]:
        """从 asmr.one API 获取元数据（DLsite 的备用源）"""
        # 提取纯数字部分
        rjcode_num = rjcode[2:] if rjcode.upper().startswith('RJ') else rjcode

        api_bases = ["https://api.asmr-200.com/api", "https://api.asmr-100.com/api"]

        for api_base in api_bases:
            url = f"{api_base}/workInfo/{rjcode_num}"
            try:
                logger.info(f"[{rjcode}] 尝试 asmr.one 备用源: {url}")
                response = self.session.get(url, timeout=(10, 30))
                if response.status_code == 200:
                    data = response.json()
                    title = data.get('title', '')
                    if not title:
                        logger.warning(f"[{rjcode}] asmr.one 返回数据无标题，跳过")
                        continue

                    metadata = WorkMetadata()
                    metadata.rjcode = rjcode
                    metadata.work_name = title

                    # 社团名
                    circle = data.get('circle', {})
                    if isinstance(circle, dict):
                        metadata.maker_name = circle.get('name', '')
                    else:
                        metadata.maker_name = data.get('name', '')

                    # 发布日期
                    metadata.release_date = data.get('release', '')[:10] if data.get('release') else ''

                    # 声优
                    for va in data.get('vas', []):
                        if isinstance(va, dict):
                            metadata.cvs.append(va.get('name', ''))

                    # 标签（优先使用中文名）
                    for tag in data.get('tags', []):
                        if isinstance(tag, dict):
                            i18n = tag.get('i18n', {})
                            zh_cn = i18n.get('zh-cn', {})
                            tag_name = zh_cn.get('name') if isinstance(zh_cn, dict) else None
                            metadata.tags.append(tag_name or tag.get('name', ''))
                        elif isinstance(tag, str):
                            metadata.tags.append(tag)

                    # 年龄分级
                    age_cat = data.get('age_category_string', '')
                    if age_cat == 'adult':
                        metadata.age_category = 'ADL'
                    elif age_cat == 'r15':
                        metadata.age_category = 'R15'
                    else:
                        metadata.age_category = 'GEN'

                    # 封面
                    metadata.cover_url = data.get('mainCoverUrl', '')

                    logger.info(f"[{rjcode}] asmr.one 元数据获取成功: {metadata.work_name}")
                    return metadata

                elif response.status_code == 404:
                    logger.info(f"[{rjcode}] asmr.one 未找到作品，尝试下一个服务器")
                    continue
                else:
                    logger.warning(f"[{rjcode}] asmr.one 返回 HTTP {response.status_code}，尝试下一个服务器")
                    continue

            except Exception as e:
                logger.warning(f"[{rjcode}] asmr.one 请求失败 ({api_base}): {e}，尝试下一个服务器")
                continue

        logger.warning(f"[{rjcode}] 所有 asmr.one 服务器均未找到作品")
        return None

    async def _fetch_from_voicehub(self, rjcode: str) -> Optional[WorkMetadata]:
        """从 voicehub.top API 获取元数据（DLsite 的备用源）"""
        if not self.config.metadata.voicehub_enabled:
            logger.info(f"[{rjcode}] voicehub.top 已禁用，跳过")
            return None

        # voicehub.top 可能接受带 RJ 前缀或不带前缀的编号
        api_urls = [
            f"https://www.voicehub.top/api/work/{rjcode}",
            f"https://www.voicehub.top/api/works/{rjcode}",
        ]

        for url in api_urls:
            try:
                logger.info(f"[{rjcode}] 尝试 voicehub.top 备用源: {url}")
                response = self.session.get(
                    url,
                    timeout=(self.config.metadata.connect_timeout, self.config.metadata.read_timeout)
                )
                if response.status_code == 200:
                    data = response.json()
                    # voicehub 的响应可能直接是作品对象，也可能是 {data: {...}} 包裹
                    if isinstance(data, dict):
                        # 有些 API 将数据包裹在 data/work 字段中
                        work = data.get('data') or data.get('work') or data

                        title = work.get('work_name') or work.get('title') or ''
                        if not title:
                            logger.warning(f"[{rjcode}] voicehub.top 返回数据无标题，跳过")
                            continue

                        metadata = WorkMetadata()
                        metadata.rjcode = work.get('workno') or work.get('rjcode') or rjcode
                        metadata.work_name = title

                        # 社团名：支持 maker_name / circle.name / circle_name
                        circle = work.get('circle', {})
                        if isinstance(circle, dict):
                            metadata.maker_name = circle.get('name', '')
                            metadata.maker_id = str(circle.get('id', ''))
                        else:
                            metadata.maker_name = work.get('maker_name', '')
                            metadata.maker_id = str(work.get('maker_id', ''))

                        # 发售日：支持 release / release_date / regist_date
                        release = work.get('release') or work.get('release_date') or work.get('regist_date', '')
                        if isinstance(release, str):
                            metadata.release_date = release[:10]

                        # 声优：支持 vas[] / cvs[] / voice_by[]
                        vas = work.get('vas') or work.get('cvs') or work.get('voice_by') or []
                        for va in vas:
                            if isinstance(va, dict):
                                metadata.cvs.append(va.get('name', ''))
                            elif isinstance(va, str):
                                metadata.cvs.append(va)

                        # 标签：支持 tags[] / genres[]
                        tags = work.get('tags') or work.get('genres') or []
                        for tag in tags:
                            if isinstance(tag, dict):
                                tag_name = tag.get('name', '')
                                if tag_name:
                                    metadata.tags.append(tag_name)
                            elif isinstance(tag, str):
                                metadata.tags.append(tag)

                        # 年龄分级：支持 age_category (数字/字符串)
                        age_cat = work.get('age_category') or work.get('age_category_string', '')
                        if isinstance(age_cat, int):
                            if age_cat == 1:
                                metadata.age_category = 'GEN'
                            elif age_cat == 2:
                                metadata.age_category = 'R15'
                            else:
                                metadata.age_category = 'ADL'
                        elif isinstance(age_cat, str):
                            age_cat_lower = age_cat.lower()
                            if 'adult' in age_cat_lower or age_cat_lower == '3':
                                metadata.age_category = 'ADL'
                            elif 'r15' in age_cat_lower or age_cat_lower == '2':
                                metadata.age_category = 'R15'
                            else:
                                metadata.age_category = 'GEN'

                        # 封面
                        metadata.cover_url = work.get('cover_url') or work.get('mainCoverUrl') or ''
                        if metadata.cover_url and metadata.cover_url.startswith('//'):
                            metadata.cover_url = 'https:' + metadata.cover_url

                        logger.info(f"[{rjcode}] voicehub.top 元数据获取成功: {metadata.work_name}")
                        return metadata

                    elif isinstance(data, list) and len(data) > 0:
                        # 可能是搜索结果的数组格式
                        work = data[0]
                        title = work.get('work_name') or work.get('title') or ''
                        if title:
                            metadata = WorkMetadata()
                            metadata.rjcode = work.get('workno') or work.get('rjcode') or rjcode
                            metadata.work_name = title
                            # 简化处理数组格式
                            circle = work.get('circle', {})
                            if isinstance(circle, dict):
                                metadata.maker_name = circle.get('name', '')
                            else:
                                metadata.maker_name = work.get('maker_name', '')
                            release = work.get('release') or work.get('release_date', '')
                            if isinstance(release, str):
                                metadata.release_date = release[:10]
                            for va in work.get('vas', []):
                                if isinstance(va, dict):
                                    metadata.cvs.append(va.get('name', ''))
                            for tag in work.get('tags', []):
                                if isinstance(tag, dict):
                                    metadata.tags.append(tag.get('name', ''))
                                elif isinstance(tag, str):
                                    metadata.tags.append(tag)
                            metadata.cover_url = work.get('cover_url') or work.get('mainCoverUrl', '')
                            if metadata.cover_url and metadata.cover_url.startswith('//'):
                                metadata.cover_url = 'https:' + metadata.cover_url
                            logger.info(f"[{rjcode}] voicehub.top 元数据获取成功(数组格式): {metadata.work_name}")
                            return metadata

                elif response.status_code == 404:
                    logger.info(f"[{rjcode}] voicehub.top 未找到作品 (404)，尝试下一个 URL")
                    continue
                else:
                    logger.warning(f"[{rjcode}] voicehub.top 返回 HTTP {response.status_code}")
                    continue

            except Exception as e:
                logger.warning(f"[{rjcode}] voicehub.top 请求失败 ({url}): {e}")

        logger.warning(f"[{rjcode}] voicehub.top 所有 URL 均未找到作品")
        return None

    async def _fetch_translated_title(self, rjcode: str, lang: str, validate_chinese: bool = True) -> Optional[str]:
        """获取指定语言的翻译标题
        
        Args:
            rjcode: RJ号
            lang: 语言代码 (如 'zh-CN', 'zh-TW')
            validate_chinese: 是否验证标题不包含日文假名（中文翻译标题通常不包含假名）
        """
        await asyncio.sleep(self.config.metadata.sleep_interval)
        
        url = f"https://www.dlsite.com/maniax/api/=/product.json?workno={rjcode}&locale={lang}"
        logger.info(f"[{rjcode}] 调用翻译标题API: {url}")
        
        try:
            response = self.session.get(
                url,
                timeout=(self.config.metadata.connect_timeout, self.config.metadata.read_timeout)
            )
            response.raise_for_status()
            
            data = response.json()
            if data and len(data) > 0:
                title = data[0].get('work_name')
                if title:
                    logger.info(f"[{rjcode}] API返回标题: {title}")
                    
                    # 验证是否包含日文假名（如果需要）
                    # 中文翻译标题通常不包含日文假名，如果包含说明可能是日文原文
                    if validate_chinese and self._contains_japanese_kana(title):
                        logger.warning(f"[{rjcode}] 标题包含日文假名，可能是日文原文而非翻译: {title}")
                        return None
                    
                    return title
            
            return None
            
        except Exception as e:
            logger.error(f"[{rjcode}] 获取翻译标题失败: {e}")
            return None
    
    def _contains_japanese_kana(self, text: str) -> bool:
        """检查文本是否包含日文假名（平假名或片假名）

        日文标题通常包含假名，而中文翻译标题通常不包含
        返回True表示可能是日文标题，False表示可能是中文标题
        """
        import re
        # 平假名范围: \u3040-\u309F
        # 片假名范围: \u30A0-\u30FF
        # 日文标点符号: \u3000-\u303F (包含全角标点)
        kana_pattern = r'[\u3040-\u309F\u30A0-\u30FF]'

        kana_count = len(re.findall(kana_pattern, text))
        total_chars = len(text.replace(' ', ''))  # 排除空格

        if total_chars == 0:
            return False

        # 如果假名占比超过5%，认为是日文标题
        kana_ratio = kana_count / total_chars
        return kana_ratio > 0.05

    async def fetch_japanese_metadata(self, rjcode: str) -> Optional[dict]:
        """
        获取日语版本的元数据
        用于重命名模板中非标题字段的日语原文

        Args:
            rjcode: RJ号

        Returns:
            日语元数据字典，包含 maker_name, cvs, tags 等字段
        """
        await asyncio.sleep(self.config.metadata.sleep_interval)

        # 使用日语 locale 获取原始数据
        url = f"https://www.dlsite.com/maniax/api/=/product.json?workno={rjcode}&locale=ja-JP"
        logger.info(f"[{rjcode}] 获取日语元数据: {url}")

        try:
            response = self.session.get(
                url,
                timeout=(self.config.metadata.connect_timeout, self.config.metadata.read_timeout)
            )
            response.raise_for_status()

            data = response.json()
            if not data or len(data) == 0:
                logger.warning(f"[{rjcode}] 日语元数据未找到")
                return None

            product = data[0]
            japanese_metadata = {
                'rjcode': product.get('workno', rjcode),
                'work_name': product.get('work_name', ''),
                'maker_id': product.get('maker_id', ''),
                'maker_name': product.get('maker_name', ''),
                'release_date': product.get('regist_date', '')[:10],
                'series_name': product.get('series_name'),
                'series_id': product.get('series_id'),
                'tags': [],
                'cvs': [],
            }

            # 标签
            for genre in product.get('genres', []):
                japanese_metadata['tags'].append(genre.get('name', ''))

            # 声优
            creators = product.get('creaters', {})
            if isinstance(creators, dict) and 'voice_by' in creators:
                for cv in creators['voice_by']:
                    japanese_metadata['cvs'].append(cv.get('name', ''))

            logger.info(f"[{rjcode}] 日语元数据获取成功: maker_name={japanese_metadata['maker_name']}, tags={len(japanese_metadata['tags'])}, cvs={len(japanese_metadata['cvs'])}")
            return japanese_metadata

        except Exception as e:
            logger.error(f"[{rjcode}] 获取日语元数据失败: {e}")
            return None
