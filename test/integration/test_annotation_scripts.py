#!/usr/bin/env python
# -*- coding: utf-8 -*-

#
# Copyright (C) 2016 University of Dundee & Open Microscopy Environment.
# All rights reserved. Use is subject to license terms supplied in LICENSE.txt
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

"""
   Integration test for annotation scripts.
"""

from __future__ import print_function
import omero
from omero.gateway import BlitzGateway
from omero.model import AnnotationAnnotationLinkI, MapAnnotationI
from omero.constants.metadata import NSCLIENTMAPANNOTATION, NSINSIGHTTAGSET
from omero.rtypes import rstring, rlist, rbool
from omero.util.temp_files import create_path
import omero.scripts

import pytest
from script import ScriptTest
from script import run_script


import_script = "/omero/annotation_scripts/Import_from_csv.py"
export_script = "/omero/annotation_scripts/Export_to_csv.py"
remove_script = "/omero/annotation_scripts/Remove_KeyVal.py"
convert_script = "/omero/annotation_scripts/Convert_KeyVal_namespace.py"

DEFAULT_IMPORT_ARGS = {
    "CSV separator": rstring("guess"),
    "Columns to exclude": rlist([
        rstring("<ID>"),
        rstring("<NAME>"),
        rstring("<PARENTS>")
    ]),
    "Target ID colname": rstring("OBJECT_ID"),
    "Target name colname": rstring("OBJECT_NAME"),
    "Exclude empty values": rbool(False),
    "Import tags": rbool(False),
    "Only use personal tags": rbool(False),
    "Allow tag creation": rbool(False),
}


def link_file_plate(client, plate, cvs_file):
    conn = BlitzGateway(client_obj=client)
    fa = conn.createFileAnnfromLocalFile(cvs_file, mimetype="text/csv")
    assert fa is not None
    assert fa.id > 0
    link = omero.model.PlateAnnotationLinkI()
    link.setParent(plate)
    link.setChild(omero.model.FileAnnotationI(fa.id, False))
    client.getSession().getUpdateService().saveAndReturnObject(link)
    return fa


class TestAnnotationScripts(ScriptTest):

    @pytest.mark.parametrize('import_tag', [True, False])
    @pytest.mark.parametrize('tag_creation', [True, False])
    @pytest.mark.parametrize('ns', [
        "", NSCLIENTMAPANNOTATION, "otherNS"
    ])
    @pytest.mark.parametrize('ns_in_csv', [True, False])
    def test_import(self, import_tag, tag_creation, ns, ns_in_csv):
        sid = super(TestAnnotationScripts, self).get_script(import_script)
        assert sid > 0

        client, user = self.new_client_and_user()

        n_well = 3
        plates = self.import_plates(client, plate_cols=n_well, plate_rows=1)
        plate = plates[0]

        cvs_file = create_path("test_kvp_name", ".csv")
        # create a file annotation

        ns_str = "NAMESPACE" + "".join([f";{ns}" for i in range(3)])
        with open(cvs_file.abspath(), 'w') as f:
            if ns_in_csv:
                f.write(ns_str + "\n")
            f.write("OBJECT_NAME; key_1; key_2; key_3\n")
            f.write("A1; val_A; val_B; val_C" + "\n")
            f.write("A2; val_D; val_E; val_F" + "\n")
            f.write("A3; val_G; val_H; val_I" + "\n")

        fa = link_file_plate(client, plate, cvs_file)

        # run the script
        args = DEFAULT_IMPORT_ARGS.copy()
        args["Data_Type"] = rstring("Plate")
        args["IDs"] = rlist([omero.rtypes.rlong(plate.id.val)])
        args["Target Data_Type"] = rstring("-- Well")
        args["File_Annotation"] = rstring(str(fa.id))
        args["Import tags"] = rbool(import_tag)
        args["Allow tag creation"] = rbool(tag_creation)
        if not ns_in_csv and ns != "":
            args["Namespace (blank for default or from csv)"] = rstring(ns)

        msg = run_script(client, sid, args, "Message")

        conn = BlitzGateway(client_obj=client)
        assert msg._val == f"Added Annotations to {n_well}/{n_well} Well(s)"
        plate_o = conn.getObject("Plate", plate.id.val)
        list_well = list(plate_o.listChildren())
        list_well = sorted(list_well, key=lambda w: w.getWellPos())

        well_a1, well_a2, well_a3 = list_well

        assert well_a1.getAnnotationCounts()["MapAnnotation"] == 1
        assert well_a2.getAnnotationCounts()["MapAnnotation"] == 1
        assert well_a3.getAnnotationCounts()["MapAnnotation"] == 1

        if ns == "":
            ns = NSCLIENTMAPANNOTATION

        value = list(well_a1.listAnnotations(ns=ns))[0].getValue()
        assert len(value) == 3
        assert value[0] == ("key_1", "val_A")
        assert value[1] == ("key_2", "val_B")
        assert value[2] == ("key_3", "val_C")

        value = list(well_a2.listAnnotations(ns=ns))[0].getValue()
        assert len(value) == 3
        assert value[0] == ("key_1", "val_D")
        assert value[1] == ("key_2", "val_E")
        assert value[2] == ("key_3", "val_F")

        value = list(well_a3.listAnnotations(ns=ns))[0].getValue()
        assert len(value) == 3
        assert value[0] == ("key_1", "val_G")
        assert value[1] == ("key_2", "val_H")
        assert value[2] == ("key_3", "val_I")

    @pytest.mark.parametrize('import_tag', [True, False])
    @pytest.mark.parametrize('tag_creation', [False])
    def test_import_tags(self, import_tag, tag_creation):
        sid = super(TestAnnotationScripts, self).get_script(import_script)
        assert sid > 0

        client, user = self.new_client_and_user()
        conn = BlitzGateway(client_obj=client)
        update = conn.getUpdateService()

        if not tag_creation:  # Create the tags ahead
            self.make_tag(name="tail", client=client)
            self.make_tag(name="head", client=client)
            self.make_tag(name="mouse", client=client)

            tagset = self.make_tag(
                name="condition", ns=NSINSIGHTTAGSET, client=client
            )
            tag1 = self.make_tag(name="ctrl", client=client)
            tag2 = self.make_tag(name="test", client=client)

            link = AnnotationAnnotationLinkI()
            link.setParent(tagset)
            link.setChild(tag1)
            update.saveObject(link)
            tagset = conn.getObject("TagAnnotation", tagset.id.val)._obj
            link = AnnotationAnnotationLinkI()
            link.setParent(tagset)
            link.setChild(tag2)
            update.saveObject(link)

        n_well = 3
        plates = self.import_plates(client, plate_cols=n_well, plate_rows=1)
        plate = plates[0]

        cvs_file = create_path("test_kvp_name", ".csv")
        # create a file annotation

        with open(cvs_file.abspath(), 'w') as f:
            f.write("OBJECT_NAME; key_1; tag; tag\n")
            f.write("A1; val_A; ctrl[condition]; mouse,tail\n")
            f.write("A2; val_B; test[condition],head;\n")
            f.write("A3; val_C; ; mouse\n")

        fa = link_file_plate(client, plate, cvs_file)

        # run the script
        args = DEFAULT_IMPORT_ARGS.copy()
        args["Data_Type"] = rstring("Plate")
        args["IDs"] = rlist([omero.rtypes.rlong(plate.id.val)])
        args["Target Data_Type"] = rstring("-- Well")
        args["File_Annotation"] = rstring(str(fa.id))
        args["Import tags"] = rbool(import_tag)
        args["Allow tag creation"] = rbool(tag_creation)

        msg = run_script(client, sid, args, "Message")

        assert msg._val == f"Added Annotations to {n_well}/{n_well} Well(s)"
        plate_o = conn.getObject("Plate", plate.id.val)
        list_well = list(plate_o.listChildren())
        list_well = sorted(list_well, key=lambda w: w.getWellPos())
        well_a1, well_a2, well_a3 = list_well

        if import_tag:
            assert well_a1.getAnnotationCounts()["TagAnnotation"] == 3
            assert well_a2.getAnnotationCounts()["TagAnnotation"] == 2
            assert well_a3.getAnnotationCounts()["TagAnnotation"] == 1
        else:
            assert well_a1.getAnnotationCounts()["TagAnnotation"] == 0
            assert well_a2.getAnnotationCounts()["TagAnnotation"] == 0
            assert well_a3.getAnnotationCounts()["TagAnnotation"] == 0

        assert well_a1.getAnnotationCounts()["MapAnnotation"] == 1
        assert well_a2.getAnnotationCounts()["MapAnnotation"] == 1
        assert well_a3.getAnnotationCounts()["MapAnnotation"] == 1

        annlist = list(well_a1.listAnnotations(ns=NSCLIENTMAPANNOTATION))
        value = annlist[0].getValue()
        assert len(value) == 1
        assert value[0] == ("key_1", "val_A")

        annlist = list(well_a2.listAnnotations(ns=NSCLIENTMAPANNOTATION))
        value = annlist[0].getValue()
        assert len(value) == 1
        assert value[0] == ("key_1", "val_B")

        annlist = list(well_a3.listAnnotations(ns=NSCLIENTMAPANNOTATION))
        value = annlist[0].getValue()
        assert len(value) == 1
        assert value[0] == ("key_1", "val_C")

    def test_import_split(self):
        sid = super(TestAnnotationScripts, self).get_script(import_script)
        assert sid > 0

        client, user = self.new_client_and_user()

        n_well = 3
        plates = self.import_plates(client, plate_cols=n_well, plate_rows=1)
        plate = plates[0]

        cvs_file = create_path("test_kvp_name", ".csv")
        # create a file annotation
        with open(cvs_file.abspath(), 'w') as f:
            f.write("OBJECT_NAME; key_1; key_2\n")
            f.write("A1; val_A,val_B; val_C\n")
            f.write("A2; val_D,val_E,val_F;\n")
            f.write("A3; ; val_G,val_H\n")

        fa = link_file_plate(client, plate, cvs_file)

        # run the script
        args = DEFAULT_IMPORT_ARGS.copy()
        args["Data_Type"] = rstring("Plate")
        args["IDs"] = rlist([omero.rtypes.rlong(plate.id.val)])
        args["Target Data_Type"] = rstring("-- Well")
        args["File_Annotation"] = rstring(str(fa.id))
        args["Split values on"] = rstring(",")
        args["Exclude empty values"] = rbool(False)

        msg = run_script(client, sid, args, "Message")
        conn = BlitzGateway(client_obj=client)
        assert msg._val == f"Added Annotations to {n_well}/{n_well} Well(s)"
        plate_o = conn.getObject("Plate", plate.id.val)
        list_well = list(plate_o.listChildren())
        list_well = sorted(list_well, key=lambda w: w.getWellPos())
        well_a1, well_a2, well_a3 = list_well

        assert well_a1.getAnnotationCounts()["MapAnnotation"] == 1
        assert well_a2.getAnnotationCounts()["MapAnnotation"] == 1
        assert well_a3.getAnnotationCounts()["MapAnnotation"] == 1

        value = list(well_a1.listAnnotations())[0].getValue()
        assert len(value) == 3
        assert value[0] == ("key_1", "val_A")
        assert value[1] == ("key_1", "val_B")
        assert value[2] == ("key_2", "val_C")

        value = list(well_a2.listAnnotations())[0].getValue()
        assert len(value) == 4
        assert value[0] == ("key_1", "val_D")
        assert value[1] == ("key_1", "val_E")
        assert value[2] == ("key_1", "val_F")
        assert value[3] == ("key_2", "")

        value = list(well_a3.listAnnotations())[0].getValue()
        assert len(value) == 3
        assert value[0] == ("key_1", "")
        assert value[1] == ("key_2", "val_G")
        assert value[2] == ("key_2", "val_H")

    def test_import_empty(self):
        sid = super(TestAnnotationScripts, self).get_script(import_script)
        assert sid > 0

        client, user = self.new_client_and_user()

        n_well = 3
        plates = self.import_plates(client, plate_cols=n_well, plate_rows=1)
        plate = plates[0]

        cvs_file = create_path("test_kvp_name", ".csv")
        # create a file annotation
        with open(cvs_file.abspath(), 'w') as f:
            f.write("OBJECT_NAME; key_1; key_2\n")
            f.write("A1; val_A;\n")
            f.write("A2; ;\n")
            f.write("A3; ; val_B\n")

        fa = link_file_plate(client, plate, cvs_file)

        # run the script
        args = DEFAULT_IMPORT_ARGS.copy()
        args["Data_Type"] = rstring("Plate")
        args["IDs"] = rlist([omero.rtypes.rlong(plate.id.val)])
        args["Target Data_Type"] = rstring("-- Well")
        args["File_Annotation"] = rstring(str(fa.id))
        args["Exclude empty values"] = rbool(True)

        msg = run_script(client, sid, args, "Message")
        conn = BlitzGateway(client_obj=client)
        assert msg._val == f"Added Annotations to {n_well-1}/{n_well} Well(s)"
        plate_o = conn.getObject("Plate", plate.id.val)
        list_well = list(plate_o.listChildren())
        list_well = sorted(list_well, key=lambda w: w.getWellPos())
        well_a1, well_a2, well_a3 = list_well

        assert well_a1.getAnnotationCounts()["MapAnnotation"] == 1
        assert well_a2.getAnnotationCounts()["MapAnnotation"] == 0
        assert well_a3.getAnnotationCounts()["MapAnnotation"] == 1

        value = list(well_a1.listAnnotations())[0].getValue()
        assert len(value) == 1
        assert value[0] == ("key_1", "val_A")

        value = list(well_a3.listAnnotations())[0].getValue()
        assert len(value) == 1
        assert value[0] == ("key_2", "val_B")

    def test_convert(self):
        sid = super(TestAnnotationScripts, self).get_script(convert_script)
        assert sid > 0

        client, user = self.new_client_and_user()
        conn = BlitzGateway(client_obj=client)
        image = self.make_image(name="testImage", client=client)

        kv = MapAnnotationI()
        kv.setMapValue([omero.model.NamedValue("key_1", "val_A")])
        kv.setNs(rstring("test"))
        kv = client.sf.getUpdateService().saveAndReturnObject(kv)
        self.link(image, kv, client=client)

        args = {
            "Data_Type": rstring("Image"),
            "IDs": rlist([omero.rtypes.rlong(image.id.val)]),
            "Target Data_Type": rstring("<on current>"),
            "Old Namespace (blank for default)": rlist([rstring("test")]),
            "New Namespace (blank for default)": rstring("new_ns"),
            "Create new and merge": rbool(False)
        }

        msg = run_script(client, sid, args, "Message")

        assert msg._val == "Updated kv pairs to 1/1 Image"

        conn = BlitzGateway(client_obj=client)
        image_o = conn.getObject("Image", image.id.val)

        value = list(image_o.listAnnotations(ns="new_ns"))[0].getValue()
        assert len(value) == 1
        assert value[0] == ("key_1", "val_A")

    def test_remove(self):
        agreement = (
            "I understand what I am doing and that this will result " +
            "in a batch deletion of key-value pairs from the server"
        )

        sid = super(TestAnnotationScripts, self).get_script(remove_script)
        assert sid > 0

        client, user = self.new_client_and_user()
        conn = BlitzGateway(client_obj=client)
        image = self.make_image(name="testImage", client=client)

        kv = MapAnnotationI()
        kv.setMapValue([omero.model.NamedValue("key_1", "val_A")])
        kv.setNs(rstring("test_delete"))
        kv = client.sf.getUpdateService().saveAndReturnObject(kv)
        self.link(image, kv, client=client)

        args = {
            "Data_Type": rstring("Image"),
            "IDs": rlist([omero.rtypes.rlong(image.id.val)]),
            "Target Data_Type": rstring("<on current>"),
            "Namespace (blank for default)": rlist([rstring("test_delete")]),
            agreement: rbool(True)
        }

        msg = run_script(client, sid, args, "Message")

        assert msg._val == "Key value data deleted from 1 of 1 objects"

        conn = BlitzGateway(client_obj=client)
        image_o = conn.getObject("Image", image.id.val)

        assert len(list(image_o.listAnnotations())) == 0
