#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
 Remove_KeyVal.py"

 Remove all key-value pairs associated with a namespace from
 objects on OMERO.

-----------------------------------------------------------------------------
  Copyright (C) 2018 - 2024
  This program is free software; you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation; either version 2 of the License, or
  (at your option) any later version.
  This program is distributed in the hope that it will be useful,
  but WITHOUT ANY WARRANTY; without even the implied warranty of
  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
  GNU General Public License for more details.
  You should have received a copy of the GNU General Public License along
  with this program; if not, write to the Free Software Foundation, Inc.,
  51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
------------------------------------------------------------------------------
Created by Christian Evenhuis

"""

from omero.gateway import BlitzGateway
import omero
from omero.rtypes import rlong, rstring, robject
from omero.constants.metadata import NSCLIENTMAPANNOTATION
import omero.scripts as scripts


CHILD_OBJECTS = {
    "Project": "Dataset",
    "Dataset": "Image",
    "Screen": "Plate",
    "Plate": "Well",
    "Well": "WellSample",
    "WellSample": "Image"
}

ALLOWED_PARAM = {
    "Project": ["Project", "Dataset", "Image"],
    "Dataset": ["Dataset", "Image"],
    "Image": ["Image"],
    "Screen": ["Screen", "Plate", "Well", "Acquisition", "Image"],
    "Plate": ["Plate", "Well", "Acquisition", "Image"],
    "Well": ["Well", "Image"],
    "Acquisition": ["Acquisition", "Image"],
    "Tag": ["Project", "Dataset", "Image",
            "Screen", "Plate", "Well", "Acquisition"]
}

P_DTYPE = "Data_Type"  # Do not change
P_IDS = "IDs"  # Do not change
P_TARG_DTYPE = "Target Data_Type"
P_NAMESPACE = "Namespace(s) (blank for default)"
P_AGREEMENT = ("I understand what I am doing and that this will result " +
               "in a batch deletion of Key-Value pairs from the server")


def get_children_recursive(source_object, target_type):
    """
    Recursively retrieve child objects of a specified type from a source
    OMERO object.

    :param source_object: The OMERO source object from which child objects
    are retrieved.
    :type source_object: omero.model.<ObjectType>
    :param target_type: The OMERO object type to be retrieved as children.
    :type target_type: str
    :return: A list of child objects of the specified target type.
    :rtype: list
    """
    if CHILD_OBJECTS[source_object.OMERO_CLASS] == target_type:
        # Stop condition, we return the source_obj children
        if source_object.OMERO_CLASS != "WellSample":
            return source_object.listChildren()
        else:
            return [source_object.getImage()]
    else:  # Not yet the target
        result = []
        for child_obj in source_object.listChildren():
            # Going down in the Hierarchy list
            result.extend(get_children_recursive(child_obj, target_type))
        return result


def target_iterator(conn, source_object, target_type, is_tag):
    """
    Iterate over and yield target objects of a specified type from a source
    OMERO object.

    :param conn: OMERO connection for server interaction.
    :type conn: omero.gateway.BlitzGateway
    :param source_object: Source OMERO object to iterate over.
    :type source_object: omero.model.<ObjectType>
    :param target_type: Target object type to retrieve.
    :type target_type: str
    :param is_tag: Flag indicating if the source object is a Tag.
    :type is_tag: bool
    :yield: Target objects of the specified type.
    :rtype: omero.model.<ObjectType>
    """
    if target_type == source_object.OMERO_CLASS:
        target_obj_l = [source_object]
    elif source_object.OMERO_CLASS == "PlateAcquisition":
        # Check if there is more than one Run, otherwise
        # it's equivalent to start from a plate (and faster this way)
        plate_o = source_object.getParent()
        wellsamp_l = get_children_recursive(plate_o, "WellSample")
        if len(list(plate_o.listPlateAcquisitions())) > 1:
            # Only case where we need to filter on PlateAcquisition
            run_id = source_object.getId()
            wellsamp_l = filter(lambda x: x._obj.plateAcquisition._id._val
                                == run_id, wellsamp_l)
        target_obj_l = [wellsamp.getImage() for wellsamp in wellsamp_l]
    elif target_type == "PlateAcquisition":
        # No direct children access from a plate
        if source_object.OMERO_CLASS == "Screen":
            plate_l = get_children_recursive(source_object, "Plate")
        elif source_object.OMERO_CLASS == "Plate":
            plate_l = [source_object]
        target_obj_l = [r for p in plate_l for r in p.listPlateAcquisitions()]
    elif is_tag:
        target_obj_l = conn.getObjectsByAnnotations(target_type,
                                                    [source_object.getId()])
        # Need that to load objects
        obj_ids = [o.getId() for o in target_obj_l]
        if len(obj_ids) > 0:
            target_obj_l = list(conn.getObjects(target_type, obj_ids))
        else:
            target_obj_l = []
    else:
        target_obj_l = get_children_recursive(source_object,
                                              target_type)

    print(f"Iterating objects from {source_object}:")
    for target_obj in target_obj_l:
        print(f"\t- {target_obj}")
        yield target_obj


def main_loop(conn, script_params):
    """
    Iterates through specified OMERO objects and removes Key-Value pair
    annotations within given Namespaces.

    :param conn: OMERO connection for server interaction.
    :type conn: omero.gateway.BlitzGateway
    :param script_params: Dictionary of script parameters including source data
        type, target data type, object IDs, and Namespace list.
    :type script_params: dict
    :return: Message indicating the success of the deletions, and the result
        object if any annotation was removed.
    :rtype: tuple
    """
    source_type = script_params[P_DTYPE]
    target_type = script_params[P_TARG_DTYPE]
    source_ids = script_params[P_IDS]
    namespace_l = script_params[P_NAMESPACE]

    nsuccess = 0
    ntotal = 0
    result_obj = None

    for source_object in conn.getObjects(source_type, source_ids):
        is_tag = source_type == "TagAnnotation"
        for target_obj in target_iterator(conn, source_object,
                                          target_type, is_tag):
            success = remove_map_annotations(conn, target_obj, namespace_l)
            if success:
                nsuccess += 1
                if result_obj is None:
                    result_obj = target_obj

            ntotal += 1
        print("\n------------------------------------\n")
    message = (f"Key-Value pairs deleted from {nsuccess} out of " +
               f"{ntotal} {target_type}s.")

    return message, result_obj


def remove_map_annotations(conn, obj, namespace_l):
    """
    Deletes map annotations within the specified Namespaces from an
    OMERO object.

    :param conn: OMERO connection for server interaction.
    :type conn: omero.gateway.BlitzGateway
    :param obj: OMERO object from which MapAnnotations will be removed.
    :type obj: omero.model.<ObjectType>
    :param namespace_l: List of Namespaces to remove annotations from; '*'
        denotes all namespaces.
    :type namespace_l: list
    :return: 1 if annotations were successfully deleted, 0 otherwise.
    :rtype: int
    """
    mapann_ids = []
    forbidden_deletion = []
    for namespace in namespace_l:
        p = {} if namespace == "*" else {"ns": namespace}
        for ann in obj.listAnnotations(**p):
            if isinstance(ann, omero.gateway.MapAnnotationWrapper):
                if ann.canDelete():  # If not, skipping it
                    mapann_ids.append(ann.id)
                else:
                    forbidden_deletion.append(ann.id)

    if len(mapann_ids) == 0:
        return 0
    print(f"\tMap Annotation IDs to delete: {mapann_ids}")
    if len(forbidden_deletion) > 0:
        print("\tMap Annotation IDs skipped (not permitted):",
              f"{forbidden_deletion}\n")
    try:
        conn.deleteObjects("Annotation", mapann_ids)
        return 1
    except Exception:
        print("Failed to delete links")
        return 0


def run_script():
    """
    Main entry point, called by the client to initiate the script, collect
    parameters, and execute annotation deletion based on user input.

    :return: Sets output messages and result objects for OMERO client session.
    :rtype: None
    """
    # Cannot add fancy layout if we want auto fill and selct of object ID
    source_types = [
                    rstring("Project"), rstring("Dataset"), rstring("Image"),
                    rstring("Screen"), rstring("Plate"), rstring("Well"),
                    rstring("Acquisition"), rstring("Image"), rstring("Tag"),
    ]

    # Duplicate Image for UI, but not a problem for script
    target_types = [
                    rstring("<selected>"), rstring("Project"),
                    rstring("- Dataset"), rstring("-- Image"),
                    rstring("Screen"), rstring("- Plate"),
                    rstring("-- Well"), rstring("-- Acquisition"),
                    rstring("--- Image"), rstring("<all (from selected)>")
    ]

    # Here we define the script name and description.
    # Good practice to put url here to give users more guidance on how to run
    # your script.
    client = scripts.client(
        'Remove Key-Value pairs',
        """
    Deletes Key-Value pairs of the selected objects.
    \t
    Check the guide for more information on parameters and errors:
    https://omero-guides.readthedocs.io/en/latest/scripts/docs/annotation_scripts.html
    \t
    Default namespace: openmicroscopy.org/omero/client/mapAnnotation
        """,  # Tabs are needed to add line breaks in the HTML

        scripts.String(
            P_DTYPE, optional=False, grouping="1",
            description="Data type of the parent objects.",
            values=source_types, default="Dataset"),

        scripts.List(
            P_IDS, optional=False, grouping="1.1",
            description="IDs of the parent objects").ofType(rlong(0)),

        scripts.String(
            P_TARG_DTYPE, optional=False, grouping="1.2",
            description="Data type to process from the selected " +
            "parent objects.",
            values=target_types, default="<selected>"),

        scripts.List(
            P_NAMESPACE, optional=True,
            grouping="1.3",
            description="Namespace(s) of the Key-Value pairs to " +
                        "delete. Client namespace by default, " +
                        "'*' for all.").ofType(rstring("")),

        scripts.Bool(
            P_AGREEMENT, optional=True, grouping="2",
            description="Make sure that you understood the scope of " +
                        "what will be deleted."),

        authors=["Christian Evenhuis", "MIF", "Tom Boissonnet"],
        institutions=["University of Technology Sydney", "CAi HHU"],
        contact="https://forum.image.sc/tag/omero",
        version="2.0.0",
    )

    try:
        params = parameters_parsing(client)
        print("Input parameters:")
        keys = [P_DTYPE, P_IDS, P_TARG_DTYPE, P_NAMESPACE]
        for k in keys:
            print(f"\t- {k}: {params[k]}")
        print("\n####################################\n")

        # wrap client to use the Blitz Gateway
        conn = BlitzGateway(client_obj=client)
        messages = []
        targets = params[P_TARG_DTYPE]
        for target in targets:  # Loop on target, use case of process all
            params[P_TARG_DTYPE] = target
            message, robj = main_loop(conn, params)
            messages.append(message)
        client.setOutput("Message", rstring(" ".join(messages)))
        if robj is not None:
            client.setOutput("Result", robject(robj._obj))
    except AssertionError as err:
        # Display assertion errors in OMERO.web activities
        client.setOutput("ERROR", rstring(err))
        raise AssertionError(str(err))
    finally:
        client.closeSession()


def parameters_parsing(client):
    """
    Parses and validates input parameters from the client, with defaults for
    optional inputs.

    :param client: Script client used to obtain input parameters.
    :type client: omero.scripts.ScriptClient
    :return: Dictionary of parsed parameters, ready for processing.
    :rtype: dict
    """
    params = {}
    # Param dict with defaults for optional parameters
    params[P_NAMESPACE] = [NSCLIENTMAPANNOTATION]

    for key in client.getInputKeys():
        if client.getInput(key):
            # unwrap rtypes to String, Integer etc
            params[key] = client.getInput(key, unwrap=True)

    assert params[P_AGREEMENT], "Please tick the box to confirm that you " +\
                                "understood the risks of a batch deletion."

    if params[P_TARG_DTYPE] == "<selected>":
        params[P_TARG_DTYPE] = params[P_DTYPE]
    elif params[P_TARG_DTYPE].startswith("-"):
        # Getting rid of the trailing '---' added for the UI
        params[P_TARG_DTYPE] = params[P_TARG_DTYPE].split(" ")[1]

    if params[P_TARG_DTYPE] != "<all (from selected)>":
        assert params[P_TARG_DTYPE] in ALLOWED_PARAM[params[P_DTYPE]], \
               (f"{params['Target Data_Type']} is not a valid target for " +
                f"{params['Data_Type']}.")

    if params[P_TARG_DTYPE] == "<all (from selected)>":
        params[P_TARG_DTYPE] = ALLOWED_PARAM[params[P_DTYPE]]
    else:
        # Convert to list for iteration over single element
        params[P_TARG_DTYPE] = [params[P_TARG_DTYPE]]
    params[P_TARG_DTYPE] = ["PlateAcquisition" if el == "Acquisition" else el
                            for el in params[P_TARG_DTYPE]]

    if params[P_DTYPE] == "Tag":
        params[P_DTYPE] = "TagAnnotation"

    # Remove duplicate entries from namespace list
    tmp = params[P_NAMESPACE]
    if "*" in tmp:
        tmp = ["*"]
    params[P_NAMESPACE] = list(set(tmp))

    return params


if __name__ == "__main__":
    run_script()
