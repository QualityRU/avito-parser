import datetime
import random
import re
import time
import xml.etree.ElementTree as ET
from urllib.parse import parse_qs, urlparse

from loguru import logger
from selenium.common.exceptions import (
    StaleElementReferenceException,
    TimeoutException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from seleniumbase import SB


class LocatorAvito:
    """Селекторы для парсинга Avito"""

    TITLES = (By.CSS_SELECTOR, "div[itemtype*='http://schema.org/Product']")
    NAME = (By.CSS_SELECTOR, "[itemprop='name']")
    DESCRIPTIONS = (By.CSS_SELECTOR, "p[style='--module-max-lines-size:4']")
    URL = (By.CSS_SELECTOR, "[itemprop='url']")
    PRICE = (By.CSS_SELECTOR, "[itemprop='price']")
    GEO = (By.CSS_SELECTOR, "div[class*='style-item-address']")
    DATE_PUBLIC = (By.CSS_SELECTOR, "[data-marker='item-view/item-date']")


class AvitoParse:
    """
    Парсинг недвижимости на Avito для ЮФО.
    Сохранение данных в XML-файлы с именем avito_{region}_{год.месяц.день часы:минуты}.
    Парсинг полей: заголовок, цена, адрес, площадь (м²), ссылка, дата публикации.
    """

    def __init__(
        self, url: str, count: int = 5, stop_event=None, region: str = None
    ):
        self.base_url = url
        self.url = url
        self.count = count
        self.data = []
        self.stop_event = stop_event
        self.region = region

    def __get_url(self):
        logger.info(f'Открываю страницу: {self.url}')
        try:
            self.driver.open(self.url)
            # Проверяем, не заблокирован ли доступ
            if 'Доступ ограничен' in self.driver.get_title():
                logger.info(
                    'Доступ ограничен: проблема с IP. Пауза перед повторной попыткой.'
                )
                time.sleep(
                    random.randint(10, 20)
                )  # Оставляем паузу для обхода блокировки
                return self.__get_url()

            # Ожидаем появления элементов объявлений (или другого ключевого элемента)
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located(LocatorAvito.TITLES)
            )
            logger.info('Страница успешно загружена.')

        except TimeoutException:
            logger.error(
                f'Превышено время ожидания загрузки элементов на странице {self.url}'
            )
            time.sleep(5)  # Пауза перед повторной попыткой при таймауте
            return self.__get_url()

        except Exception as e:
            logger.error(f'Ошибка при открытии страницы {self.url}: {e}')
            time.sleep(5)  # Пауза перед повторной попыткой при других ошибках
            return self.__get_url()

    def __paginator(self):
        logger.info('Страница загружена. Просматриваю объявления')
        for i in range(self.count):
            if self.stop_event and self.stop_event.is_set():
                break
            try:
                self.driver.execute_script(
                    'window.scrollTo(0, document.body.scrollHeight);'
                )
                time.sleep(1)
            except Exception:
                logger.debug('Не удалось прокрутить страницу, продолжаю.')
            self.__parse_page()
            time.sleep(random.randint(2, 4))
            self.open_next_btn()
        if self.data:  # Сохраняем остатки, если есть
            self.__save_to_xml()

    def open_next_btn(self):
        self.url = self.get_next_page_url(self.url)
        logger.info(f'Переход на следующую страницу: {self.url}')
        self.driver.open(self.url)

    def get_next_page_url(self, url: str):
        try:
            url_parts = urlparse(url)
            query_params = parse_qs(url_parts.query)
            current_page = int(query_params.get('p', [1])[0])
            next_page = current_page + 1
            next_url = f'{self.base_url}?p={next_page}'
            return next_url
        except Exception as err:
            logger.error(
                f'Ошибка формирования ссылки на следующую страницу для {url}: {err}'
            )
            return url

    def __parse_page(self):
        if self.stop_event and self.stop_event.is_set():
            logger.info('Процесс остановлен')
            return
        current_url = (
            self.driver.get_current_url()
        )  # Сохраняем текущий URL списка
        ads_elements = self.driver.find_elements(*LocatorAvito.TITLES)
        if ads_elements:
            logger.info(f'Найдено объявлений: {len(ads_elements)}')
        else:
            logger.info('Объявления не найдены на странице.')
            return

        for i in range(len(ads_elements)):
            retries = 3
            while retries > 0:
                try:
                    ads_elements = self.driver.find_elements(
                        *LocatorAvito.TITLES
                    )
                    ad = ads_elements[i]
                    ad_data = {}

                    ad_data['name'] = ad.find_element(*LocatorAvito.NAME).text

                    description = ''
                    if ad.find_elements(*LocatorAvito.DESCRIPTIONS):
                        try:
                            description = ad.find_element(
                                *LocatorAvito.DESCRIPTIONS
                            ).text
                        except Exception as err:
                            logger.debug(f'Ошибка получения описания: {err}')

                    ad_data['url'] = ad.find_element(
                        *LocatorAvito.URL
                    ).get_attribute('href')

                    price = ad.find_element(*LocatorAvito.PRICE).get_attribute(
                        'content'
                    )
                    ad_data['price'] = price
                    int(price)  # Проверка, что цена — число

                    # Извлекаем площадь сначала из title, затем из description, если не найдено
                    area = self.__extract_area(
                        ad_data['name']
                    )  # Сначала проверяем title
                    if (
                        not area
                    ):  # Если в title нет площади, проверяем description
                        area = self.__extract_area(description)
                    ad_data['area'] = area

                    detail_data = self.__parse_detail(ad_data['url'])
                    ad_data['date_public'] = detail_data.get('date_public', '')
                    ad_data['address'] = detail_data.get('address', '')

                    self.data.append(ad_data)
                    logger.info(
                        f'Добавлено объявление [{len(self.data)}/2000]'
                    )

                    if len(self.data) >= 2000:
                        self.__save_to_xml()
                        self.data = []  # Очищаем список после сохранения

                    self.driver.open(current_url)
                    time.sleep(1)  # Даем странице загрузиться
                    break

                except StaleElementReferenceException as e:
                    logger.debug(
                        f'Устаревший элемент, повторная попытка ({retries} осталось): {e}'
                    )
                    retries -= 1
                    time.sleep(1)
                except Exception as e:
                    logger.debug(f'Не удалось обработать объявление: {e}')
                    break

    def __extract_area(self, text):
        pattern = r'(\d+(?:[.,]\d+)?)\s*(м²|кв\.?м|квадратных метров)'
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1)
        return ''

    def __parse_detail(self, url):
        detail_data = {}
        try:
            self.driver.open(url)
            time.sleep(2)
            if 'Доступ ограничен' in self.driver.get_title():
                logger.info('Доступ ограничен на детальной странице. Пауза.')
                time.sleep(random.randint(10, 20))
                return self.__parse_detail(url)
            date_elements = self.driver.find_elements(
                *LocatorAvito.DATE_PUBLIC
            )
            if date_elements:
                detail_data['date_public'] = date_elements[0].text
            else:
                detail_data['date_public'] = ''
            geo_elements = self.driver.find_elements(*LocatorAvito.GEO)
            if geo_elements:
                detail_data['address'] = geo_elements[0].text
            else:
                detail_data['address'] = ''
        except Exception as e:
            logger.debug(f'Ошибка при парсинге детали объявления {url}: {e}')
            detail_data['date_public'] = ''
            detail_data['address'] = ''
        return detail_data

    def __save_to_xml(self):
        if not self.data:
            logger.info('Нет данных для сохранения.')
            return
        root = ET.Element('real_estate')
        for ad in self.data:
            ad_element = ET.SubElement(root, 'ad')
            ET.SubElement(ad_element, 'title').text = ad.get('name', '')
            ET.SubElement(ad_element, 'price').text = ad.get('price', '')
            ET.SubElement(ad_element, 'address').text = ad.get('address', '')
            ET.SubElement(ad_element, 'area').text = ad.get('area', '')
            ET.SubElement(ad_element, 'url').text = ad.get('url', '')
            ET.SubElement(ad_element, 'date').text = ad.get('date_public', '')
        # Формируем имя файла с текущей датой и временем
        timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        file_name = f'avito_{self.region}_{timestamp}.xml'
        tree = ET.ElementTree(root)
        tree.write(file_name, encoding='utf-8', xml_declaration=True)
        logger.info(
            f'Сохранён файл {file_name} с {len(self.data)} объявлениями.'
        )

    def parse(self):
        with SB(
            uc=False,
            headed=False,
            headless=True,
            page_load_strategy='eager',
            block_images=True,
        ) as self.driver:
            try:
                self.__get_url()
                self.__paginator()
            except Exception as err:
                logger.error(f'Ошибка в процессе парсинга: {err}')
                if self.data:
                    self.__save_to_xml()
        logger.info('Парсинг завершен.')


if __name__ == '__main__':
    UFO_REGIONS = [
        'krasnodarskiy_kray',
        'adygeya',
        'astrahanskaya_oblast',
        'volgogradskaya_oblast',
        'kalmykiya',
        'rostovskaya_oblast',
        'respublika_krym',
        'sevastopol',
    ]
    for region in UFO_REGIONS:
        base_url = f'https://www.avito.ru/{region}/kvartiry/prodam'
        logger.info(f'Начинаю парсинг региона: {region}')
        avito_parser = AvitoParse(
            url=base_url,
            count=35,  # Количество страниц для парсинга на регион
            region=region,  # Передаем регион
        )
        avito_parser.parse()
