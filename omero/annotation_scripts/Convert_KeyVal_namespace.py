# coding=utf-8
"""
 Convert_KeyVal_namespace.py

 Convert the namespace of objects key-value pairs.
-----------------------------------------------------------------------------
  Copyright (C) 2024
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
Created by Tom Boissonnet

"""

import omero
from omero.gateway import BlitzGateway
from omero.rtypes import rstring, rlong, robject
import omero.scripts as scripts
from omero.constants.metadata import NSCLIENTMAPANNOTATION


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
P_OLD_NS = "Input Namespace(s) (blank for default)"
P_NEW_NS = "New Namespace (blank for default)"
P_MERGE = "Create new and merge"


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
    :param is_tag: Flag indicating if the source object is a tag.
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
    Process OMERO objects, updating or merging Namespaces of Key-Value
    annotations.

    This function iterates over objects, identifies annotations with specified
    Namespaces, and either updates or merges them according to provided
    parameters.

    :param conn: OMERO connection object for database operations.
    :type conn: omero.gateway.BlitzGateway
    :param script_params: Dictionary of parameters required by the script.
    :type script_params: dict
    :return: Summary message indicating update counts, and the result object.
    :rtype: tuple
    """
    source_type = script_params[P_DTYPE]
    target_type = script_params[P_TARG_DTYPE]
    source_ids = script_params[P_IDS]
    old_namespace = script_params[P_OLD_NS]
    new_namespace = script_params[P_NEW_NS]
    merge = script_params[P_MERGE]

    ntarget_processed = 0
    ntarget_updated = 0
    result_obj = None

    # One file output per given ID
    for source_object in conn.getObjects(source_type, source_ids):
        is_tag = source_type == "TagAnnotation"
        for target_obj in target_iterator(conn, source_object,
                                          target_type, is_tag):
            ntarget_processed += 1
            keyval_l, ann_l = get_existing_map_annotations(target_obj,
                                                           old_namespace)
            if len(keyval_l) > 0:
                if merge:
                    annotate_object(conn, target_obj, keyval_l,
                                    new_namespace)
                    remove_map_annotations(conn, ann_l)
                else:
                    for ann in ann_l:
                        try:
                            ann.setNs(new_namespace)
                            ann.save()
                        except Exception:
                            print(f"Failed to edit {ann}")
                            continue
                ntarget_updated += 1
                if result_obj is None:
                    result_obj = target_obj
            else:
                print("\tNo MapAnnotation found with that Namespace\n")
        print("\n------------------------------------\n")
    message = (
        "Updated Key-Value pairs to " +
        f"{ntarget_updated}/{ntarget_processed} {target_type}."
    )

    return message, result_obj


def get_existing_map_annotations(obj, namespace_l):
    """
    Retrieve existing map annotations with specified Namespaces from an
    OMERO object.

    :param obj: OMERO object from which annotations are retrieved.
    :type obj: omero.model.<ObjectType>
    :param namespace_l: List of namespaces used to filter annotations.
    :type namespace_l: list of str
    :return: A tuple containing a list of Key-Value pairs and a list of
        MapAnnotation objects.
    :rtype: tuple
    """
    keyval_l, ann_l = [], []
    forbidden_deletion = []
    for namespace in namespace_l:
        p = {} if namespace == "*" else {"ns": namespace}
        for ann in obj.listAnnotations(**p):
            if isinstance(ann, omero.gateway.MapAnnotationWrapper):
                if ann.canEdit():  # If not, skipping it
                    keyval_l.extend([(k, v) for (k, v) in ann.getValue()])
                    ann_l.append(ann)
                else:
                    forbidden_deletion.append(ann.id)
    if len(forbidden_deletion) > 0:
        print("\tMap Annotation IDs skipped (not permitted):",
              f"{forbidden_deletion}")
    return keyval_l, ann_l


def remove_map_annotations(conn, ann_l):
    """
    Delete specified MapAnnotations from OMERO.

    :param conn: OMERO connection for server interaction.
    :type conn: omero.gateway.BlitzGateway
    :param ann_l: List of map annotation objects to delete.
    :type ann_l: list of omero.model.MapAnnotationWrapper
    :return: Returns 1 if deletion succeeds, otherwise 0.
    :rtype: int
    """
    mapann_ids = [ann.id for ann in ann_l]

    if len(mapann_ids) == 0:
        return 0
    print(f"\tMap Annotation IDs to delete: {mapann_ids}\n")
    try:
        conn.deleteObjects("Annotation", mapann_ids)
        return 1
    except Exception:
        print(f"Failed to delete old annotations {mapann_ids}")
        return 0


def annotate_object(conn, obj, kv_list, namespace):
    """
    Create a new MapAnnotation with specified Key-Value pairs on an
    OMERO object.

    :param conn: OMERO connection object for annotation.
    :type conn: omero.gateway.BlitzGateway
    :param obj: OMERO object to annotate.
    :type obj: omero.model.<ObjectType>
    :param kv_list: Key-Value pairs to include in the annotation.
    :type kv_list: list of tuples
    :param namespace: Namespace for the new annotation.
    :type namespace: str
    :return: The annotation is linked to the object within the OMERO database.
    :rtype: None
    """
    map_ann = omero.gateway.MapAnnotationWrapper(conn)
    map_ann.setNs(namespace)
    map_ann.setValue(kv_list)
    map_ann.save()

    print("\tMap Annotation created", map_ann.id)
    obj.linkAnnotation(map_ann)


def run_script():
    """
    Execute the OMERO script to convert Namespaces for key-value pair
    annotations.

    This function initializes the script parameters, processes input from the
    OMERO client, and orchestrates the execution of the main script logic,
    including handling target data types and merging annotations.

    :param None: This function does not take any parameters.
    :return: This function does not return a value; it sets outputs directly to
        the client.
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

    client = scripts.client(
        'Convert Key-Value pairs namespace',
        """
    Converts the Namespace of Key-Value pairs.
    \t
    Check the guide for more information on parameters and errors:
    https://omero-guides.readthedocs.io/en/latest/scripts/docs/annotation_scripts.html
    \t
    Default Namespace: openmicroscopy.org/omero/client/mapAnnotation
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
            P_OLD_NS, optional=True, grouping="1.4",
            description="Namespace(s) of the Key-Value pairs to " +
                        "process. Client namespace by default, " +
                        "'*' for all.").ofType(rstring("")),

        scripts.String(
            P_NEW_NS, optional=True, grouping="1.5",
            description="The new Namespace for the annotations."),

        scripts.Bool(
            P_MERGE, optional=True, grouping="1.6",
            description="Check to merge selected Key-Value pairs " +
                        "into a single new one (will also include " +
                        "Key-Value pairs already in the 'New Namespace')",
                        default=False),

        authors=["Tom Boissonnet"],
        institutions=["CAi HHU"],
        contact="https://forum.image.sc/tag/omero",
        version="2.0.0",
    )

    try:
        params = parameters_parsing(client)
        print("Input parameters:")
        keys = [P_DTYPE, P_IDS, P_TARG_DTYPE, P_OLD_NS, P_NEW_NS]
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
    Parse input parameters from the OMERO client, establishing defaults and
    validating specific combinations for data types and Namespaces.

    :param client: The OMERO client object from which input parameters are
    retrieved.
    :type client: omero.gateway.BlitzGateway
    :return: A dictionary of parsed parameters, including validated and default
        values for processing the script logic.
    :rtype: dict
    """
    params = {}
    # Param dict with defaults for optional parameters
    params[P_OLD_NS] = [NSCLIENTMAPANNOTATION]
    params[P_NEW_NS] = NSCLIENTMAPANNOTATION

    for key in client.getInputKeys():
        if client.getInput(key):
            params[key] = client.getInput(key, unwrap=True)

    if params[P_TARG_DTYPE] == "<selected>":
        params[P_TARG_DTYPE] = params[P_DTYPE]
    elif params[P_TARG_DTYPE].startswith("-"):
        # Getting rid of any trailing '--- ' added for the UI
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

    if params[P_MERGE]:
        # If merge, also include existing target NS
        params[P_OLD_NS].append(params[P_NEW_NS])
    # Remove duplicate entries from namespace list
    tmp = params[P_OLD_NS]
    if "*" in tmp:
        tmp = ["*"]
    params[P_OLD_NS] = list(set(tmp))

    return params


if __name__ == "__main__":
    run_script()
