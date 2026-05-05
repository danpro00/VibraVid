# 01.03.24

import json
import logging

from bs4 import BeautifulSoup

from VibraVid.utils.http_client import create_client, get_headers
from VibraVid.services._base.object import SeasonManager, Episode, Season


logger = logging.getLogger(__name__)


class GetSerieInfo:
    def __init__(self, url, media_id: int = None, series_name: str = None, year: int = None, provider_language: str = "it", series_display_name: str = None):
        """
        Initialize the GetSerieInfo class for scraping TV series information.
        
        Args:
            - url (str): The URL of the streaming site.
            - media_id (int): Unique identifier for the media
            - series_name (str): Slug of the TV series
            - series_display_name (str): Name of the TV series
        """
        self.is_series = False
        self.headers = get_headers()
        self.url = url
        self.media_id = media_id
        self.year = year
        self.seasons_manager = SeasonManager()
        self.provider_language = provider_language
        if isinstance(self.url, str) and self.url.endswith(('/it', '/en')):
            self.base_url = self.url.rsplit('/', 1)[0]
        else:
            self.base_url = self.url

        if series_name is not None:
            self.is_series = True
            self.series_name = series_name  # slug, used for URL building
            self.series_display_name = series_display_name if series_display_name is not None else series_name

    def collect_info_title(self) -> None:
        """
        Retrieve general information about the TV series from the streaming site.
        
        Raises:
            Exception: If there's an error fetching series information
        """
        try:
            client = create_client(headers=self.headers)
            response = client.get(f"{self.url}/titles/{self.media_id}-{self.series_name}")
            client.close()
            response.raise_for_status()

            # Extract series info from JSON response
            soup = BeautifulSoup(response.text, "html.parser")
            json_response = json.loads(soup.find("div", {"id": "app"}).get("data-page"))
            self.version = json_response['version']
            
            # Extract information about available seasons
            title_data = json_response.get("props", {}).get("title", {})
            
            # Save general series information
            self.title_info = title_data
            
            # Extract available seasons and add them to SeasonManager
            seasons_data = title_data.get("seasons", [])
            for season_data in seasons_data:
                self.seasons_manager.add(Season(
                    id=season_data.get('id'),
                    number=season_data.get('number'),
                    name=f"Season {season_data.get('number')}",
                    slug=season_data.get('slug')
                ))

        except Exception as e:
            logger.error(f"Error collecting series info: {e}")
            raise

    def collect_info_season(self, number_season: int) -> None:
        """
        Retrieve episode information for a specific season.
        
        Args:
            number_season (int): Season number to fetch episodes for
        
        Raises:
            Exception: If there's an error fetching episode information
        """
        try:
            # Get the season object from SeasonManager
            season = self.seasons_manager.get_season_by_number(number_season)
            if not season:
                logger.error(f"Season {number_season} not found")
                return

            # We'll aggregate episodes from both Italian and English catalogs
            episodes_by_lang = {}

            # Determine if this is the last season -> only then attach language info
            try:
                last_season_number = max([s.number for s in self.seasons_manager.seasons])
            except Exception:
                last_season_number = number_season

            include_language = (number_season == last_season_number)

            for lang in ['it', 'en']:
                try:
                    logger.info(f"Fetching episodes for season {number_season} in language '{lang}'")
                    client = create_client(headers=self.headers)
                    resp_ver = client.get(f"{self.base_url}/{lang}")
                    resp_ver.raise_for_status()
                    ver = BeautifulSoup(resp_ver.text, "html.parser")
                    ver = json.loads(ver.find("div", {"id": "app"}).get("data-page"))['version']
                    client.close()
                except Exception:
                    # Skip this language if we can't get version
                    continue

                custom_headers = self.headers.copy()
                custom_headers.update({
                    'x-inertia': 'true',
                    'x-inertia-version': ver,
                })
                client = create_client(headers=custom_headers)
                try:
                    response = client.get(f"{self.base_url}/{lang}/titles/{self.media_id}-{self.series_name}/season-{number_season}")
                    response.raise_for_status()
                    json_response = response.json().get('props', {}).get('loadedSeason', {}).get('episodes', [])
                except Exception as e:
                    logger.debug(f"No season data for lang {lang}: {e}")
                    json_response = []
                finally:
                    client.close()

                episodes_by_lang[lang] = json_response

            # Merge episodes from both languages
            def _merge_and_add(ep_lists_by_lang: dict, season_obj: Season, attach_language: bool = False):
                logger.info(f"Merging episodes for season {season_obj.number} with language attachment: {attach_language}")
                merged = {}
                for lang, ep_list in ep_lists_by_lang.items():
                    for ep in ep_list:
                        num = ep.get('number')
                        if num is None:
                            # fallback to id if number missing
                            num = ep.get('id')

                        if num in merged:
                            # already exists from other language -> mark both
                            existing = merged[num]
                            if attach_language:
                                prev_lang = getattr(existing, 'language', None)
                                if prev_lang:
                                    # combine languages uniquely
                                    langs = set(str(prev_lang).split(',')) | {lang}
                                    existing.language = ','.join(sorted(langs))
                        else:
                            kwargs = {}
                            if attach_language:
                                kwargs['language'] = lang

                            merged[num] = Episode(
                                id=ep.get('id'),
                                video_id=ep.get('id'),
                                number=ep.get('number'),
                                name=ep.get('name'),
                                duration=ep.get('duration'),
                                **kwargs
                            )

                # Add episodes to season in order
                for key in sorted(merged.keys()):
                    season_obj.episodes.add(merged[key])

            _merge_and_add(episodes_by_lang, season, include_language)

        except Exception as e:
            logger.error(f"Error collecting episodes for season {number_season}: {e}")
            raise

    
    # ------------- FOR GUI -------------
    def getNumberSeason(self) -> int:
        """
        Get the total number of seasons available for the series.
        """
        if not self.seasons_manager.seasons:
            self.collect_info_title()
            
        return len(self.seasons_manager.seasons)
    
    def getEpisodeSeasons(self, season_number: int) -> list:
        """
        Get all episodes for a specific season.
        """
        season = self.seasons_manager.get_season_by_number(season_number)

        if not season:
            logger.error(f"Season {season_number} not found")
            return []
            
        if not season.episodes.episodes:
            self.collect_info_season(season_number)
            
        return season.episodes.episodes