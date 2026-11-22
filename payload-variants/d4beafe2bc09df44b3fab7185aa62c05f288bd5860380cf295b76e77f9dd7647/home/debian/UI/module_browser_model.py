# import sys
# import psutil

from PySide2.QtGui import QGuiApplication
from PySide2.QtQml import QQmlApplicationEngine, qmlRegisterType
from PySide2.QtCore import QUrl, QTimer, QAbstractListModel, QModelIndex

from PySide2.QtCore import QObject, Signal, Slot, Property, Qt

import module_info

"""
module info for browsing

needs: title, description, long_description, tags
"""
module_browser_singleton = None
current_search = ""
showing_favourite = False
effect_prototypes  = {k:module_info.effect_prototypes_models_all[k] for k in sorted(module_info.effect_prototypes_models_all) if k in module_info.effect_type_maps["beebo"] and k not in {"poly_rhythm", "poly_rhythm_legacy", "ableton_link_audio", "sample_box_legacy"}}
filtered_modules = effect_prototypes
favourites = {}
current_tags = set()

class ModuleBrowserModel(QAbstractListModel):

    EffectType = Qt.UserRole + 0
    Description = Qt.UserRole + 1
    LongDescription = Qt.UserRole + 2
    Tags = Qt.UserRole + 3
    Favourite = Qt.UserRole + 4
    PickerTitle = Qt.UserRole + 6
    DisplayName = Qt.UserRole + 5

    def __init__(self, l_favourites):
        QAbstractListModel.__init__(self)
        self._picker_titles = {
            "ableton_link": "Ableton Link",
            "link_audio_receive": "Ableton Link Audio Receive",
            "link_audio_send": "Ableton Link Audio Send",
        }

        # self.__effects_count = 3
        self.__effect_types = filtered_modules.keys()
        self._sync_order()
        global module_browser_singleton
        global favourites
        favourites = l_favourites
        module_browser_singleton = self


    def startInsert(self):
        # print("start insert")
        self.beginResetModel()

    def endInsert(self):
        # print("end insert")
        self._sync_order()
        self.endResetModel()

    def startRemove(self, effect_id):
        current_row = list(self.__order.values()).index(effect_id)
        self.beginRemoveRows(QModelIndex(), current_row, current_row)
        # print(effect_id, " removing.")

    def endRemove(self):
        self._sync_order()
        self.endRemoveRows()

    def _picker_title(self, effect_type):
        return self._picker_titles.get(effect_type, effect_type.replace("_", " "))

    def _sync_order(self):
        def key(effect_type):
            title = " ".join(self._picker_title(effect_type).casefold().split())
            return title, effect_type.casefold(), effect_type

        self.__order = dict(enumerate(sorted(filtered_modules, key=key)))

    @Slot('QVariantMap')
    def set_picker_titles(self, titles):
        if titles != self._picker_titles:
            self.beginResetModel()
            self._picker_titles = dict(titles)
            self._sync_order()
            self.endResetModel()

    @Slot()
    def clear_filter(self):
        global current_search
        current_search = ""

        self.beginResetModel()
        global filtered_modules

        filtered_modules = effect_prototypes
        self._sync_order()
        self.endResetModel()

    @Slot(bool)
    def show_favourites(self, v):
        global showing_favourite
        showing_favourite = v
        self.add_filter(current_search)

    @Slot(str, bool)
    def show_hide_tag(self, tag, v):
        if v:
            current_tags.add(tag)
        else:
            current_tags.discard(tag)

        self.add_filter(current_search)

    @Slot()
    def clear_tags(self):
        current_tags.clear()
        self.add_filter(current_search)

    @Slot(str)
    def add_filter(self, search=""):
        global current_search
        current_search = search

        self.beginResetModel()
        global filtered_modules

        # print("before len filtered modules", len(filtered_modules))
        if showing_favourite:
            filtered_modules = {k:v for (k,v) in effect_prototypes.items() if k in favourites["modules"]}
        else:
            filtered_modules = effect_prototypes

        if current_tags:
            filtered_modules = {k:v for (k,v) in filtered_modules.items() if current_tags.issubset(v["tags"]) }

        query = " ".join(current_search.casefold().split())
        if query:
            def matches(key, prototype):
                names = (key.replace("_", " "), prototype.get("display_name", ""))
                names = [" ".join(name.casefold().split()) for name in names]
                if len(query) == 1:
                    return any(name.startswith(query) for name in names)
                description = " ".join(prototype["description"].casefold().split())
                return any(query in name for name in names) or query in description
            filtered_modules = {k: v for k, v in filtered_modules.items() if matches(k, v)}

            # print("len filtered modules", len(filtered_modules))
        self._sync_order()
        self.endResetModel()

    @Slot()
    def items_changed(self):
        self.dataChanged.emit(self.index(0,0), self.index(len(filtered_modules)-1, 0))

    @Slot()
    def item_changed(self, effect_id):
        current_row = list(self.__order.values()).index(effect_id)
        # self.__cpu_load = psutil.cpu_percent(percpu=True)
        self.dataChanged.emit(self.index(current_row,0), self.index(current_row, 0))

    def rowCount(self, parent=None):
        # return self.__effects_count
        # print("getting rowcount", len(filtered_modules))
        return len(filtered_modules)

    def data(self, index, role):
        if not index.isValid():
            return QVariant()
        row = index.row()
        if 0 <= row < self.rowCount():
            # print("getting role", role, filtered_modules)
            if role == ModuleBrowserModel.EffectType:
                # print("getting effectID", filtered_modules[self.__order[index.row()]])
                return self.__order[index.row()]
            elif role == ModuleBrowserModel.PickerTitle:
                return self._picker_title(self.__order[row])
            elif role == ModuleBrowserModel.Description:
                # print("getting effectType")
                return filtered_modules[self.__order[index.row()]]["description"]
            elif role == ModuleBrowserModel.LongDescription:
                return filtered_modules[self.__order[index.row()]]["long_description"]
            elif role == ModuleBrowserModel.Tags:
                # print("getting tags", filtered_modules[self.__order[index.row()]]["tags"])
                return list(filtered_modules[self.__order[index.row()]]["tags"])
            elif role == ModuleBrowserModel.Favourite:
                return self.__order[index.row()] in favourites["modules"]
            elif role == ModuleBrowserModel.DisplayName:
                return filtered_modules[self.__order[index.row()]].get("display_name", "")
        return QVariant()

    def roleNames(self):
        return {
            ModuleBrowserModel.EffectType: b"l_effect_type",
            ModuleBrowserModel.Description: b"description",
            ModuleBrowserModel.LongDescription: b"long_description",
            ModuleBrowserModel.Tags: b"tags",
            ModuleBrowserModel.Favourite: b"is_favourite",
            ModuleBrowserModel.PickerTitle: b"picker_title",
            ModuleBrowserModel.DisplayName: b"display_name",
        }

