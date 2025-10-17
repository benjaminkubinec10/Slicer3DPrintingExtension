import logging
import os
from typing import Annotated, Optional
import tempfile
import subprocess
import json

from qt import QFileDialog

import re

import vtk

import slicer
from slicer.i18n import tr as _
from slicer.i18n import translate
from slicer.ScriptedLoadableModule import *
from slicer.util import VTKObservationMixin
from slicer.parameterNodeWrapper import (
    parameterNodeWrapper,
    WithinRange,
)

from slicer import vtkMRMLSegmentationNode, vtkMRMLScalarVolumeNode

#
# VoxelPrintAuto
#


class VoxelPrintAuto(ScriptedLoadableModule):
    """Uses ScriptedLoadableModule base class, available at:
    https://github.com/Slicer/Slicer/blob/main/Base/Python/slicer/ScriptedLoadableModule.py
    """

    def __init__(self, parent):
        ScriptedLoadableModule.__init__(self, parent)
        self.parent.title = _("VoxelPrintAuto")  # TODO: make this more human readable by adding spaces
        # TODO: set categories (folders where the module shows up in the module selector)
        self.parent.categories = [translate("qSlicerAbstractCoreModule", "3D Printing")]
        self.parent.dependencies = []  # TODO: add here list of module names that this module requires
        self.parent.contributors = ["John Doe (AnyWare Corp.)"]  # TODO: replace with "Firstname Lastname (Organization)"
        # TODO: update with short description of the module and a link to online module documentation
        # _() function marks text as translatable to other languages
        self.parent.helpText = _("""
This is an example of scripted loadable module bundled in an extension.
See more information in <a href="https://github.com/organization/projectname#VoxelPrintAuto">module documentation</a>.
""")
        # TODO: replace with organization, grant and thanks
        self.parent.acknowledgementText = _("""
This file was originally developed by Jean-Christophe Fillion-Robin, Kitware Inc., Andras Lasso, PerkLab,
and Steve Pieper, Isomics, Inc. and was partially funded by NIH grant 3P41RR013218-12S1.
""")

        # Additional initialization step after application startup is complete
        slicer.app.connect("startupCompleted()", registerSampleData)


#
# Register sample data sets in Sample Data module
#


def registerSampleData():
    """Add data sets to Sample Data module."""
    # It is always recommended to provide sample data for users to make it easy to try the module,
    # but if no sample data is available then this method (and associated startupCompeted signal connection) can be removed.

    import SampleData

    iconsPath = os.path.join(os.path.dirname(__file__), "Resources/Icons")

    # To ensure that the source code repository remains small (can be downloaded and installed quickly)
    # it is recommended to store data sets that are larger than a few MB in a Github release.

    # VoxelPrintAuto1
    SampleData.SampleDataLogic.registerCustomSampleDataSource(
        # Category and sample name displayed in Sample Data module
        category="VoxelPrintAuto",
        sampleName="VoxelPrintAuto1",
        # Thumbnail should have size of approximately 260x280 pixels and stored in Resources/Icons folder.
        # It can be created by Screen Capture module, "Capture all views" option enabled, "Number of images" set to "Single".
        thumbnailFileName=os.path.join(iconsPath, "VoxelPrintAuto1.png"),
        # Download URL and target file name
        uris="https://github.com/Slicer/SlicerTestingData/releases/download/SHA256/998cb522173839c78657f4bc0ea907cea09fd04e44601f17c82ea27927937b95",
        fileNames="VoxelPrintAuto1.nrrd",
        # Checksum to ensure file integrity. Can be computed by this command:
        #  import hashlib; print(hashlib.sha256(open(filename, "rb").read()).hexdigest())
        checksums="SHA256:998cb522173839c78657f4bc0ea907cea09fd04e44601f17c82ea27927937b95",
        # This node name will be used when the data set is loaded
        nodeNames="VoxelPrintAuto1",
    )

    # VoxelPrintAuto2
    SampleData.SampleDataLogic.registerCustomSampleDataSource(
        # Category and sample name displayed in Sample Data module
        category="VoxelPrintAuto",
        sampleName="VoxelPrintAuto2",
        thumbnailFileName=os.path.join(iconsPath, "VoxelPrintAuto2.png"),
        # Download URL and target file name
        uris="https://github.com/Slicer/SlicerTestingData/releases/download/SHA256/1a64f3f422eb3d1c9b093d1a18da354b13bcf307907c66317e2463ee530b7a97",
        fileNames="VoxelPrintAuto2.nrrd",
        checksums="SHA256:1a64f3f422eb3d1c9b093d1a18da354b13bcf307907c66317e2463ee530b7a97",
        # This node name will be used when the data set is loaded
        nodeNames="VoxelPrintAuto2",
    )


#
# VoxelPrintAutoParameterNode
#


@parameterNodeWrapper
class VoxelPrintAutoParameterNode:

    inputSegmentation: vtkMRMLSegmentationNode #selected segmentation
    outputFilePath: str = "" #path where G-Code will be saved
    slicerPath: str = "" #path of bambu CLI
    stlPath: str = "" #temp stl file path
    printerBrand: str = "" #name of the printer brand
    printerModel: str = "" #model of the printer
    nozzleSize: str = ""
    processProfilePath: str = "" #path of process profile


#
# VoxelPrintAutoWidget
#


class VoxelPrintAutoWidget(ScriptedLoadableModuleWidget, VTKObservationMixin):
    """Uses ScriptedLoadableModuleWidget base class, available at:
    https://github.com/Slicer/Slicer/blob/main/Base/Python/slicer/ScriptedLoadableModule.py
    """

    def __init__(self, parent=None) -> None:
        """Called when the user opens the module the first time and the widget is initialized."""
        ScriptedLoadableModuleWidget.__init__(self, parent)
        VTKObservationMixin.__init__(self)  # needed for parameter node observation
        self.logic = None
        self._parameterNode = None
        self._parameterNodeGuiTag = None
        self._defaultSlicerPath = None

    def setup(self) -> None:
        """Called when the user opens the module the first time and the widget is initialized."""
        ScriptedLoadableModuleWidget.setup(self)

        # Load widget from .ui file (created by Qt Designer).
        uiWidget = slicer.util.loadUI(self.resourcePath("UI/VoxelPrintAuto.ui"))
        self.layout.addWidget(uiWidget)
        self.ui = slicer.util.childWidgetVariables(uiWidget)

        # Set scene in MRML widgets. Make sure that in Qt designer the top-level qMRMLWidget's
        uiWidget.setMRMLScene(slicer.mrmlScene)
        
        # Create logic class. Logic implements all computations that should be possible to run
        self.logic = VoxelPrintAutoLogic()

        # These connections ensure that we update parameter node when scene is closed
        self.addObserver(slicer.mrmlScene, slicer.mrmlScene.StartCloseEvent, self.onSceneStartClose)
        self.addObserver(slicer.mrmlScene, slicer.mrmlScene.EndCloseEvent, self.onSceneEndClose)

        #Connect buttons
        self.ui.generateGcodeButton.connect("clicked(bool)", self.onGenerateGcodeButton) #Generate G-code button
        self.ui.outputBrowseButton.connect("clicked()", self.onBrowseOutputPath)
        
        #Setup input segmentation comboBox
        self.ui.inputComboBox.setMRMLScene(slicer.mrmlScene)
        self.ui.inputComboBox.connect("currentNodeChanged(vtkMRMLNode*)", self.onInputSegmentationChanged)
        
        #Setup printer selection combo box
        self.printers = {
            "Bambu Lab": ["A1", "X1 Carbon", "P1S"]
        }
        brands = list(self.printers.keys())
        self.ui.printerBrandComboBox.addItems(brands)
        self.ui.printerBrandComboBox.setCurrentIndex(0)
        self.ui.printerBrandComboBox.connect("currentIndexChanged(int)", self.onPrinterBrandComboBoxChanged)
        self.onPrinterBrandComboBoxChanged(None)
        
        self.ui.printerModelComboBox.connect("currentIndexChanged(int)", self.onPrinterModelComboBoxChanged)
        self.onPrinterModelComboBoxChanged(None)
        
        self.nozzle = ["0.2 nozzle", "0.4 nozzle", "0.6 nozzle", "0.8 nozzle"]
        self.ui.nozzleComboBox.addItems(self.nozzle)
        self.ui.nozzleComboBox.setCurrentIndex(1)
        self.ui.nozzleComboBox.connect("currentIndexChanged(int)", self.onNozzleComboBoxChanged)
        self.onNozzleComboBoxChanged(None)
        
        #setup process profile combo box
        defaultPrinterBrand = self.ui.printerBrandComboBox.currentText
        defaultPrinterModel = self.ui.printerModelComboBox.currentText
        defaultNozzle = self.ui.nozzleComboBox.currentText
        
        compatibleProcessProfiles = self.logic.findProcessProfile(defaultPrinterBrand, defaultPrinterModel, defaultNozzle)
        self.ui.processProfileComboBox.addItems(compatibleProcessProfiles)
        self.ui.processProfileComboBox.setCurrentIndex(0)
        self.ui.processProfileComboBox.connect("currentIndexChanged(int)", self.onProcessProfileComboBoxChanged)
        
        
        
        
        #setup slicer comboBox
        self.setupSlicerComboBox()
        self.ui.slicerComboBox.connect("currentIndexChanged(int)", self.onSlicerComboBoxChanged)
        
        #Initialize parameter node
        self.initializeParameterNode()
        
        #setup default slicerPath in parameter node
        if self._parameterNode and not getattr(self._parameterNode, "slicerPath", None):
            self._parameterNode.slicerPath = getattr(self, "_defaultSlicerPath", None)

    def cleanup(self) -> None:
        """Called when the application closes and the module widget is destroyed."""
        self.removeObservers()

    def enter(self) -> None:
        """Called each time the user opens this module."""
        # Make sure parameter node exists and observed
        self.initializeParameterNode()

    def exit(self) -> None:
        """Called each time the user opens a different module."""
        # Do not react to parameter node changes (GUI will be updated when the user enters into the module)
        if self._parameterNode:
            self._parameterNode.disconnectGui(self._parameterNodeGuiTag)
            self._parameterNodeGuiTag = None
            self.removeObserver(self._parameterNode, vtk.vtkCommand.ModifiedEvent, self._checkCanApply)

    def onSceneStartClose(self, caller, event) -> None:
        """Called just before the scene is closed."""
        # Parameter node will be reset, do not use it anymore
        self.setParameterNode(None)

    def onSceneEndClose(self, caller, event) -> None:
        """Called just after the scene is closed."""
        # If this module is shown while the scene is closed then recreate a new parameter node immediately
        if self.parent.isEntered:
            self.initializeParameterNode()

    def initializeParameterNode(self) -> None:
        """Ensure parameter node exists and observed."""
        # Parameter node stores all user choices in parameter values, node selections, etc.
        # so that when the scene is saved and reloaded, these settings are restored.

        self.setParameterNode(self.logic.getParameterNode())

        # Select default input nodes if nothing is selected yet to save a few clicks for the user
        if not self._parameterNode.inputSegmentation:
            firstVolumeNode = slicer.mrmlScene.GetFirstNodeByClass("vtkMRMLSegmentationNode")
            if firstVolumeNode:
                self._parameterNode.inputSegmentation = firstVolumeNode
        
        #set initial printer information in parameterNode
        if not self._parameterNode.printerBrand:
            self._parameterNode.printerBrand = self.ui.printerBrandComboBox.currentText
        if not self._parameterNode.printerModel:
            self._parameterNode.printerModel = self.ui.printerModelComboBox.currentText
        if not self._parameterNode.nozzleSize:
            self._parameterNode.nozzleSize = self.ui.nozzleComboBox.currentText

    def setParameterNode(self, inputParameterNode: Optional[VoxelPrintAutoParameterNode]) -> None:
        """
        Set and observe parameter node.
        Observation is needed because when the parameter node is changed then the GUI must be updated immediately.
        """

        if self._parameterNode:
            self._parameterNode.disconnectGui(self._parameterNodeGuiTag)
            self.removeObserver(self._parameterNode, vtk.vtkCommand.ModifiedEvent, self._checkCanApply)
        self._parameterNode = inputParameterNode
        if self._parameterNode:
            # Note: in the .ui file, a Qt dynamic property called "SlicerParameterName" is set on each
            # ui element that needs connection.
            self._parameterNodeGuiTag = self._parameterNode.connectGui(self.ui)
            self.addObserver(self._parameterNode, vtk.vtkCommand.ModifiedEvent, self._checkCanApply)
            self._checkCanApply()

    def _checkCanApply(self, caller=None, event=None) -> None:
        
        if not self._parameterNode:
            self.ui.generateGcodeButton.enabled = False
            self.ui.generateGcodeButton.toolTip = _("Parameter node is not initialized")
            return
        
        inputReady = self._parameterNode.inputSegmentation is not None
        slicerReady = self._parameterNode.slicerPath and os.path.exists(self._parameterNode.slicerPath)
        outputReady = bool(self._parameterNode.outputFilePath) is not None
        
        if inputReady and slicerReady and outputReady:
            self.ui.generateGcodeButton.enabled = True
            self.ui.generateGcodeButton.toolTip = _("Ready to generate G_code")
        else:
            self.ui.generateGcodeButton.enabled = False
            missing = []
            if not inputReady:
                missing.append("input segmentation")
            if not slicerReady:
                missing.append("Slicer CLI path")
            if not outputReady:
                missing.append("output file path")
            self.ui.generateGcodeButton.toolTip = _("Missing: " + ", ".join(missing))
    
    def onGenerateGcodeButton(self) -> None:
        """Run processing when user clicks "Generate G-Code" button."""
        with slicer.util.tryWithErrorDisplay(_("Failed to generate G-code."), waitCursor=True):
            #Clear log
            self.ui.logTextBox.clear()
            self.ui.logTextBox.append("Starting G-code generation...")
            
            if not self._parameterNode:
                self.initializeParameterNode()
           
            #get input segmentation and output path
            inputSegmentation = self._parameterNode.inputSegmentation
            outputPath = self._parameterNode.outputFilePath
            slicerPath = self._parameterNode.slicerPath #path to slicer CLI
            
            self.ui.logTextBox.append(f"Parameter node slicerPath: {self._parameterNode.slicerPath}")
            self.ui.logTextBox.append(f"os.path.exists(slicerPath) = {os.path.exists(self._parameterNode.slicerPath)}")
            
            if not inputSegmentation:
                raise ValueError("No input segmentation selected.")
            if not outputPath:
                raise ValueError("No output file path specified")
            if not slicerPath or not os.path.exists(slicerPath):
                raise ValueError("Slicer CLI path is invalid or not set")
            
            #export segemtation to temp stl file
            stlPath = self.logic.exportSegmentationToSTL(inputSegmentation)
            
            self.ui.logTextBox.append("")
            self.ui.logTextBox.append(f"Segment exported to temporary STL: {stlPath}")
            self.ui.logTextBox.append(f"Running external slicer CLI: {slicerPath}")
            
            outputDir = os.path.dirname(outputPath)
            outputFilename = os.path.basename(outputPath)
            
            machineFile = self.logic.findMachineProfile(self._parameterNode.printerBrand, self._parameterNode.printerModel, self._parameterNode.nozzleSize)
            self.logic.addG92E0ToGcode(machineFile)
            
            filamentFile = "/Applications/OrcaSlicer.app/Contents/Resources/profiles/BBL/filament/Bambu PLA Aero @BBL A1.json"
            processFile = self._parameterNode.processProfilePath
            self.ui.logTextBox.append(f"Process profile selected: {processFile}")
            self.ui.logTextBox.append(f"Machine profile selected: {machineFile}")
            self.ui.logTextBox.append("")
            
            #command for slicer CLI
            command = [
                slicerPath,
                "--arrange", "1",
                "--orient", "1",
                "--load-settings", f"{machineFile};{processFile}",
                "--load-filaments", filamentFile,
                "--slice", "0",
                "--export-3mf", f"{outputDir}//{outputFilename}.3mf",
                "--export-slicedata", outputDir,
                "--info",
                stlPath
            ]
            
            #run slicer
            result = subprocess.run(command, capture_output=True, text=True)
            
            #show logs 
            self.ui.logTextBox.append(result.stdout)
            self.ui.logTextBox.append(result.stderr)
            
            self.ui.logTextBox.append("G-Code generation finished.")
            self.ui.logTextBox.append(self._parameterNode.printerBrand)
            self.ui.logTextBox.append(self._parameterNode.printerModel)
            self.ui.logTextBox.append(self._parameterNode.nozzleSize)
           
           
    def onInputSegmentationChanged(self, newNode) -> None:
        #update current selected input node
        if self._parameterNode:
            self._parameterNode.inputSegmentation = newNode

    def onBrowseOutputPath(self) -> None:
        #open file dialog
        filePath = QFileDialog.getSaveFileName(
            None, #parent widget
            "Select output file",
            "",
            "G-code files"
        )
        if filePath:
            #write path to outputPathLineEdit 
            self.ui.outputPathLineEdit.setText(filePath)
            #update parameterNode
            if self._parameterNode:
                self._parameterNode.outputFilePath = filePath
                
    def setupSlicerComboBox(self) -> None:
        #expected path
        defaultPaths = []
        defaultPaths.append("/Applications/OrcaSlicer.app/Contents/MacOS/OrcaSlicer")
        
        #check if path exists
        existingPaths = []
        for path in defaultPaths:
            if os.path.exists(path):
                existingPaths.append(path)
                
        #fill combobox with existing paths
        self.ui.slicerComboBox.blockSignals(True)
        self.ui.slicerComboBox.clear()
        
        if existingPaths:
            #Shows "Bambu Studio" in combobox and saves path as itemData
            self.ui.slicerComboBox.addItem("Orca Slicer", existingPaths[0])
            #store default path to apply late if parameter node not ready yet
            self._defaultSlicerPath = existingPaths[0]
            
            if self._parameterNode:
                self._parameterNode.slicerPath = existingPaths[0]
                
        else:
            #placeholder with None
            self.ui.slicerComboBox.addItem("Select Slicer…", None)
            self._defaultSlicerPath = None
            
        self.ui.slicerComboBox.blockSignals(False)
            
        #connect browse button
        try:
            self.ui.slicerBrowseButton.disconnect("clicked(bool)")
        except Exception:
            pass
        self.ui.slicerBrowseButton.connect("clicked(bool)", self.onBrowseSlicer)
        
    def onBrowseSlicer(self) -> None:
        #open file dialog
        filePath = QFileDialog.getOpenFileName(
            None, 
            "Select Slicer",
            "",
            "Executable files (*)"
        )
        
        if isinstance(filePath, tuple):
            filePath = filePath[0]
        
        if filePath and filePath != "":
            #clear combobox
            self.ui.slicerComboBox.blockSignals(True)
            self.ui.slicerComboBox.clear()
            #add selected path to the combobox
            self.ui.slicerComboBox.addItem(os.path.basename(filePath), filePath)
            self.ui.slicerComboBox.blockSignals(False)
            
            #update parameter node
            if self._parameterNode:
                self._parameterNode.slicerPath = filePath
            else:
                self._defaultSlicerPath = filePath
                
            #update button state
            self._checkCanApply()
            
    def onSlicerComboBoxChanged(self, index):
        if index < 0:
            return
        
        selectedPath = self.ui.slicerComboBox.itemData(index)
        
        if isinstance(selectedPath, (list, tuple)):
            selectedPath = selectedPath[0] if selectedPath else None
            
        if selectedPath:
            if self._parameterNode:
                self._parameterNode.slicerPath = selectedPath
            else:
                self._defaultSlicerPath = selectedPath
        
        self._checkCanApply()
    
    def onPrinterBrandComboBoxChanged(self, index) -> None:
        printerBrand = self.ui.printerBrandComboBox.currentText #get current selected printer brand
        printerModel = self.printers.get(printerBrand, []) #get printer models for selected brand
        nozzleSize = self.ui.nozzleComboBox.currentText
        
        if printerBrand:
            if self._parameterNode:
                self._parameterNode.printerBrand = printerBrand
        
        modelComboBox = self.ui.printerModelComboBox 
        modelComboBox.blockSignals(True) 
        modelComboBox.clear() #clear combo box
        modelComboBox.addItems(printerModel) #add printer models to combobox
        
        if printerModel:
            modelComboBox.setCurrentIndex(0)
        modelComboBox.blockSignals(False)
        
        processProfile = self.logic.findProcessProfile(printerBrand, printerModel[0], nozzleSize)
        
        processComboBox = self.ui.processProfileComboBox
        processComboBox.blockSignals(True)
        processComboBox.clear()
        processComboBox.addItems(processProfile)
        
        if processProfile:
            processComboBox.setCurrentIndex(0)
            self.onProcessProfileComboBoxChanged(None)
        processComboBox.blockSignals(False)
        
    def onPrinterModelComboBoxChanged(self, index) -> None:
        model = self.ui.printerModelComboBox.currentText
        nozzle = self.ui.nozzleComboBox.currentText
        printerBrand = self.ui.printerBrandComboBox.currentText
        
        if self._parameterNode:
            self._parameterNode.printerModel = model
            
        processProfile = self.logic.findProcessProfile(printerBrand, model, nozzle)
        
        processComboBox = self.ui.processProfileComboBox
        processComboBox.blockSignals(True)
        processComboBox.clear()
        processComboBox.addItems(processProfile)
        
        if processProfile:
            processComboBox.setCurrentIndex(0)
            self.onProcessProfileComboBoxChanged(None)
        processComboBox.blockSignals(False)
        
    
    def onNozzleComboBoxChanged(self, index) -> None:
        model = self.ui.printerModelComboBox.currentText
        nozzle = self.ui.nozzleComboBox.currentText
        printerBrand = self.ui.printerBrandComboBox.currentText
        
        if self._parameterNode:
            self._parameterNode.nozzleSize = nozzle
        
        processProfile = self.logic.findProcessProfile(printerBrand, model, nozzle)
        
        processComboBox = self.ui.processProfileComboBox
        processComboBox.blockSignals(True)
        processComboBox.clear()
        processComboBox.addItems(processProfile)
        
        if processProfile:
            processComboBox.setCurrentIndex(0)
            self.onProcessProfileComboBoxChanged(None)
        processComboBox.blockSignals(False)
    
    def onProcessProfileComboBoxChanged(self, index) -> None:
        printerBrand = self.ui.printerBrandComboBox.currentText
        processProfile = self.ui.processProfileComboBox.currentText
        currentDir = os.path.dirname(__file__)
        
        if printerBrand == "Bambu Lab":
            printerBrandDir = "BBL"
        
        processDir = os.path.join(currentDir, "Resources", "Profiles", printerBrandDir, "process")
        
        
        if self._parameterNode:
            processProfilePath = os.path.join(processDir, processProfile)
            if os.path.exists(processProfilePath):
                self._parameterNode.processProfilePath = processProfilePath
            
        
        
        
            
            
      
            

#
# VoxelPrintAutoLogic
#


class VoxelPrintAutoLogic(ScriptedLoadableModuleLogic):
    """This class should implement all the actual
    computation done by your module.  The interface
    should be such that other python code can import
    this class and make use of the functionality without
    requiring an instance of the Widget.
    Uses ScriptedLoadableModuleLogic base class, available at:
    https://github.com/Slicer/Slicer/blob/main/Base/Python/slicer/ScriptedLoadableModule.py
    """
    
    def __init__(self) -> None:
        """Called when the logic class is instantiated. Can be used for initializing member variables."""
        ScriptedLoadableModuleLogic.__init__(self)

    def getParameterNode(self):
        return VoxelPrintAutoParameterNode(super().getParameterNode())

   
    def exportSegmentationToSTL(self, segmentationNode: vtkMRMLSegmentationNode) -> str:
        #Exports selected segmentation to a temp STL file and returns its path
        
        if not segmentationNode:
            raise ValueError("Segmentation node is invalid")
        
        #temp dir
        tempDir = tempfile.mkdtemp(prefix="VoxelPrint_")
        stlFileName = f"{segmentationNode.GetName()}.stl"
        stlPath = os.path.join(tempDir, stlFileName)
        
        segmentation = segmentationNode.GetSegmentation()
        segmentIDs = vtk.vtkStringArray()
        segmentation.GetSegmentIDs(segmentIDs)
        
        #export to one stl file
        success = slicer.modules.segmentations.logic().ExportSegmentsClosedSurfaceRepresentationToFiles(
            tempDir,
            segmentationNode,
            segmentIDs, #export all segments
            "Closed surface",
            "STL", #file format
            True,
            1.0, #scale
        )
        
        if not success:
            raise RuntimeError(f"Failed to export segmentation {segmentationNode.GetName()} to STL")
        
        if not os.path.exists(stlPath):
            stlFiles = []
            for f in os.listdir(tempDir):
                if f.lower().endswith(".stl"):
                    stlFiles.append
            
            if stlFiles:
                stlPath = os.path.join(tempDir, stlFiles[0])
            else:
                raise RuntimeError(f"No STL file created in {tempDir}")

        return stlPath
    
    def findProcessProfile(self, printerBrand, printerModel, nozzle):
        currentDir = os.path.dirname(__file__)
        
        if printerBrand == "Bambu Lab":
            printerBrandDir = "BBL"
                
        processDir = os.path.join(currentDir, "Resources", "Profiles", printerBrandDir, "process")
        
        if not os.path.exists(processDir):
            return []
        
        compatibleProfiles = []
        
        if printerModel == "P1S" or printerModel == "X1 Carbon" or printerModel == "X1E" or printerModel == "X1":
            printerModel = "X1C"
        if printerModel == "A1 mini":
            printerModel = "A1M"
        
        printerModelRegex = rf"{re.escape(printerModel)}(?![A-Za-z0-9])"
        
        for filename in os.listdir(processDir):
            if filename.endswith(".json"):
                if re.search(printerModelRegex, filename):
                    if nozzle == "0.4 nozzle":
                        if "nozzle" not in filename.lower():
                            compatibleProfiles.append(filename)
                    else:
                        if nozzle in filename.lower():
                            compatibleProfiles.append(filename)
        
        return compatibleProfiles
    
    def findMachineProfile(self, printerBrand, printerModel, nozzle):
        currentDir = os.path.dirname(__file__)
        
        if printerBrand == "Bambu Lab":
            printerBrandDir = "BBL"
                
        machineDir = os.path.join(currentDir, "Resources", "Profiles", printerBrandDir, "machine")
        
        if not os.path.exists(machineDir):
            return []
        
        printerModelRegex = rf"{re.escape(printerModel)}($| \d)"
        
        for filename in os.listdir(machineDir):
            if filename.endswith(".json"):
                if re.search(printerModelRegex, filename):
                    if nozzle in filename.lower():
                        machineProfile = filename
                        
        machineProfilePath = os.path.join(machineDir, machineProfile)
        print(machineProfilePath)
        
        return machineProfilePath
    
    def addG92E0ToGcode(self, jsonPath) -> None:
        with open(jsonPath, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        data["before_layer_change_gcode"] = "G92 E0 ; zero the extruder"
        
        with open(jsonPath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            
            


#
# VoxelPrintAutoTest
#


class VoxelPrintAutoTest(ScriptedLoadableModuleTest):
    """
    This is the test case for your scripted module.
    Uses ScriptedLoadableModuleTest base class, available at:
    https://github.com/Slicer/Slicer/blob/main/Base/Python/slicer/ScriptedLoadableModule.py
    """

    def setUp(self):
        """Do whatever is needed to reset the state - typically a scene clear will be enough."""
        slicer.mrmlScene.Clear()

    def runTest(self):
        """Run as few or as many tests as needed here."""
        self.setUp()
        self.test_VoxelPrintAuto1()

    def test_VoxelPrintAuto1(self):
        """Ideally you should have several levels of tests.  At the lowest level
        tests should exercise the functionality of the logic with different inputs
        (both valid and invalid).  At higher levels your tests should emulate the
        way the user would interact with your code and confirm that it still works
        the way you intended.
        One of the most important features of the tests is that it should alert other
        developers when their changes will have an impact on the behavior of your
        module.  For example, if a developer removes a feature that you depend on,
        your test should break so they know that the feature is needed.
        """

        self.delayDisplay("Starting the test")

        # Get/create input data

        import SampleData

        registerSampleData()
        inputSegmentation = SampleData.downloadSample("VoxelPrintAuto1")
        self.delayDisplay("Loaded test data set")

        inputScalarRange = inputSegmentation.GetImageData().GetScalarRange()
        self.assertEqual(inputScalarRange[0], 0)
        self.assertEqual(inputScalarRange[1], 695)

        outputVolume = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLScalarVolumeNode")
        threshold = 100

        # Test the module logic

        logic = VoxelPrintAutoLogic()

        # Test algorithm with non-inverted threshold
        outputScalarRange = outputVolume.GetImageData().GetScalarRange()
        self.assertEqual(outputScalarRange[0], inputScalarRange[0])
        self.assertEqual(outputScalarRange[1], threshold)

        # Test algorithm with inverted threshold
        outputScalarRange = outputVolume.GetImageData().GetScalarRange()
        self.assertEqual(outputScalarRange[0], inputScalarRange[0])
        self.assertEqual(outputScalarRange[1], inputScalarRange[1])

        self.delayDisplay("Test passed")
