import logging
import os
from typing import Annotated, Optional
import tempfile
import subprocess
import json
import re

from qt import QFileDialog
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
        # _() function marks text as translatable to other languages
        self.parent.title = _("VoxelPrintAuto")
        self.parent.categories = [translate("qSlicerAbstractCoreModule", "3D Printing")]
        self.parent.dependencies = []  # TODO: add here list of module names that this module requires
        self.parent.contributors = ["John Doe (AnyWare Corp.)"]  # TODO: replace with "Firstname Lastname (Organization)"
        self.parent.helpText = _("Automated 3D printing workflow for anatomical segmentations.") # TODO: Link to online module documentation
        self.parent.acknowledgementText = _("""""")# TODO: replace with organization, grant and thanks


#
# VoxelPrintAutoParameterNode
#


@parameterNodeWrapper
class VoxelPrintAutoParameterNode:

    inputSegmentation: vtkMRMLSegmentationNode #selected segmentation
    outputFilePath: str = "" #path where G-Code will be saved
    slicerPath: str = "" #path of bambu CLI
    printerBrand: str = "" #name of the printer brand
    printerModel: str = "" #model of the printer
    nozzleSize: str = ""
    processProfilePath: str = "" #path of process profile
    filamentProfilePath: str = ""
    filamentType: str = ""


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
        
        self.ui.printerModelComboBox.connect("currentIndexChanged(int)", self.onPrinterModelComboBoxChanged)
        
        self.onPrinterBrandComboBoxChanged(0)
        
        self.nozzle = ["0.2 nozzle", "0.4 nozzle", "0.6 nozzle", "0.8 nozzle"]
        self.ui.nozzleComboBox.addItems(self.nozzle)
        self.ui.nozzleComboBox.setCurrentIndex(1)
        self.ui.nozzleComboBox.connect("currentIndexChanged(int)", self.onNozzleComboBoxChanged)
        
        #setup process profile combo box
        self.ui.processProfileComboBox.connect("currentIndexChanged(int)", self.onProcessProfileComboBoxChanged)
        
        #setup filament combobox
        self.filamentType = ["PLA", "PETG", "ABS", "ASA", "PVA", "HIPS", "PC"]
        self.ui.filamentTypeComboBox.addItems(self.filamentType)
        self.ui.filamentTypeComboBox.connect("currentIndexChanged(int)", self.onFilamentTypeComboBoxChanged)
        self.ui.filamentProfileComboBox.connect("currentIndexChanged(int)", self.onFilamentProfileComboBoxChanged)
        
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
        if not self._parameterNode.filamentType:
            self._parameterNode.filamentType = self.ui.filamentTypeComboBox.currentText
            
        printerBrand = self._parameterNode.printerBrand
        printerModel = self._parameterNode.printerModel
        nozzleSize = self._parameterNode.nozzleSize
        filamentType = self._parameterNode.filamentType
        
        self.updateProcessProfileComboBox(printerBrand, printerModel, nozzleSize)
        self.updateFilamentProfileComboBox(printerBrand, printerModel, nozzleSize, filamentType)
        
        

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
        
        #checks if all necessary values for generating gcode are set
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
            
            #log slicer path used
            self.ui.logTextBox.append(f"Parameter node slicerPath: {self._parameterNode.slicerPath}")
            self.ui.logTextBox.append(f"os.path.exists(slicerPath) = {os.path.exists(self._parameterNode.slicerPath)}")
            
            #raise errors if key values are missing
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
            
            #get output directory and name
            outputDir = os.path.dirname(outputPath)
            outputFilename = os.path.basename(outputPath)
            
            #prepare machine profile for gcode generation
            machineFile = self.logic.findMachineProfile(self._parameterNode.printerBrand, self._parameterNode.printerModel, self._parameterNode.nozzleSize)
            self.logic.addG92E0ToGcode(machineFile)
            
            #get selected filament profile
            filamentFile = self._parameterNode.filamentProfilePath
            
            #create temporary filament profile if user changes any values
            changes = self.getFilamentValueChanges(filamentFile)
            tempFilamentFile = self.logic.createTempFilamentProfile(filamentFile, changes)
            
            selectedFilamentFile = filamentFile
            filamentFile = tempFilamentFile
            
            #get process profile
            processFile = self._parameterNode.processProfilePath
            
            #log paths to all used profiles
            self.ui.logTextBox.append(f"Process profile selected: {processFile}")
            self.ui.logTextBox.append(f"Machine profile selected: {machineFile}")
            self.ui.logTextBox.append(f"Filament profile selected: {filamentFile}")
            self.ui.logTextBox.append("")
            
            #command for slicer CLI
            command = [
                slicerPath, #path to selected slicer client
                "--arrange", "1", #spread objects on printer plate to avoid overlapping
                "--orient", "1", #find best orientation and rotate object
                "--load-settings", f"{machineFile};{processFile}", #load machine profile ad process profile
                "--load-filaments", filamentFile, #load filament profile
                "--slice", "0", #slice object without opening GUI
                "--export-3mf", f"{outputDir}//{outputFilename}.3mf", #export to a .3mf file
                "--export-slicedata", outputDir, #export slice details
                "--info", #log info 
                stlPath #path to stl of sliced object
            ]
            
            #run slicer
            result = subprocess.run(command, capture_output=True, text=True) #"command" needed commands to run slicer "capture_output=True" saves output logs instead of printing them out, "text=True" saves output as strings instead of bytes
            
            #show logs 
            self.ui.logTextBox.append(result.stdout) #log standard output
            self.ui.logTextBox.append(result.stderr) #log errors
            
            self.ui.logTextBox.append("G-Code generation finished.")
            self.ui.logTextBox.append(self._parameterNode.printerBrand)
            self.ui.logTextBox.append(self._parameterNode.printerModel)
            self.ui.logTextBox.append(self._parameterNode.nozzleSize)
            
            #remove temporary filament profile after slicing
            if tempFilamentFile != selectedFilamentFile and os.path.exists(tempFilamentFile):
                if "Temp" in os.path.basename(tempFilamentFile):
                    os.remove(tempFilamentFile)
           
           
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
            #Shows "Orca Slicer" in combobox and saves path as itemData
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
        
        if isinstance(filePath, tuple): #change filePath from tuple to string
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
        if index < 0: #end function if slicer isn't selected
            return
        
        selectedPath = self.ui.slicerComboBox.itemData(index)
        
        if isinstance(selectedPath, (list, tuple)): #make sure path is saved as string
            if selectedPath:
                selectedPath = selectedPath[0]
            else:
                selectedPath = None
            
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
        filamentType = self.ui.filamentTypeComboBox.currentText
        
        if printerBrand:
            if self._parameterNode:
                self._parameterNode.printerBrand = printerBrand #save selected printer brand to parameter node
        
        modelComboBox = self.ui.printerModelComboBox 
        modelComboBox.blockSignals(True) 
        modelComboBox.clear() #clear combo box
        modelComboBox.addItems(printerModel) #add printer models to combobox
        
        if printerModel:
            modelComboBox.setCurrentIndex(0)
        modelComboBox.blockSignals(False)
        
        #update process profile and filament profile if printer brand changes
        self.updateProcessProfileComboBox(printerBrand, printerModel[0], nozzleSize)
        self.updateFilamentProfileComboBox(printerBrand, printerModel[0], nozzleSize, filamentType)
        
    def onPrinterModelComboBoxChanged(self, index) -> None:
        printerModel = self.ui.printerModelComboBox.currentText
        nozzleSize = self.ui.nozzleComboBox.currentText
        printerBrand = self.ui.printerBrandComboBox.currentText
        filamentType = self.ui.filamentTypeComboBox.currentText
        
        if self._parameterNode:
            self._parameterNode.printerModel = printerModel #save selected printer model to parameter node
        
        #update process profile and filament profile if printer brand changes
        self.updateProcessProfileComboBox(printerBrand, printerModel, nozzleSize)
        self.updateFilamentProfileComboBox(printerBrand, printerModel, nozzleSize, filamentType)
        
    
    def onNozzleComboBoxChanged(self, index) -> None:
        printerModel = self.ui.printerModelComboBox.currentText
        nozzleSize = self.ui.nozzleComboBox.currentText
        printerBrand = self.ui.printerBrandComboBox.currentText
        filamentType = self.ui.filamentTypeComboBox.currentText
        
        if self._parameterNode:
            self._parameterNode.nozzleSize = nozzleSize #save selected nozzle size to parameter node
        
        #update process profile and filament profile if printer brand changes
        self.updateProcessProfileComboBox(printerBrand, printerModel, nozzleSize)
        self.updateFilamentProfileComboBox(printerBrand, printerModel, nozzleSize, filamentType)
    
    def onProcessProfileComboBoxChanged(self, index) -> None:
        #get path to newly selected process profile 
        
        printerBrand = self.ui.printerBrandComboBox.currentText
        processProfile = self.ui.processProfileComboBox.currentText
        currentDir = os.path.dirname(__file__)
        
        if printerBrand == "Bambu Lab":
            printerBrandDir = "BBL"
        
        processDir = os.path.join(currentDir, "Resources", "Profiles", printerBrandDir, "process")
        
        #save path to selected process profile to parameter node
        if self._parameterNode:
            processProfilePath = os.path.join(processDir, processProfile)
            if os.path.exists(processProfilePath):
                self._parameterNode.processProfilePath = processProfilePath
                
    def updateProcessProfileComboBox(self, printerBrand, printerModel, nozzleSize):
        #changes selectable process profiles in combo box
        
        processProfile = self.logic.findProcessProfile(printerBrand, printerModel, nozzleSize)
        
        processComboBox = self.ui.processProfileComboBox
        processComboBox.blockSignals(True)
        processComboBox.clear()
        processComboBox.addItems(processProfile)
        
        if processProfile:
            processComboBox.setCurrentIndex(0)
            self.onProcessProfileComboBoxChanged(None)
        processComboBox.blockSignals(False)
        
    def onFilamentProfileComboBoxChanged(self, index) -> None:
        printerBrand = self.ui.printerBrandComboBox.currentText
        filamentProfile = self.ui.filamentProfileComboBox.currentText
        currentDir = os.path.dirname(__file__)
        
        if printerBrand == "Bambu Lab":
            printerBrandDir = "BBL"
            
        filamentDir = os.path.join(currentDir, "Resources", "Profiles", printerBrandDir, "filament")
        
        #get path to selected filament profile
        if self._parameterNode:
            filamentProfilePath = os.path.join(filamentDir, filamentProfile)
            if os.path.exists(filamentProfilePath):
                self._parameterNode.filamentProfilePath = filamentProfilePath #save filament profile path to parameter node
        
        #get values from newly selected filament profile   
        self.loadFilamentValues()
        
    def updateFilamentProfileComboBox(self, printerBrand, printerModel, nozzleSize, filamentType) -> None:
        #changes selectable filament profiles in combo box
        
        filamentProfile = self.logic.findFilamentProfile(printerBrand, printerModel, nozzleSize, filamentType)
        
        filamentComboBox = self.ui.filamentProfileComboBox
        filamentComboBox.blockSignals(True)
        filamentComboBox.clear()
        filamentComboBox.addItems(filamentProfile)
        
        if filamentProfile:
            filamentComboBox.setCurrentIndex(0)
            self.onFilamentProfileComboBoxChanged(None)
        filamentComboBox.blockSignals(False)
        
    def onFilamentTypeComboBoxChanged(self, index) -> None:
        printerModel = self.ui.printerModelComboBox.currentText
        nozzleSize = self.ui.nozzleComboBox.currentText
        printerBrand = self.ui.printerBrandComboBox.currentText
        filamentType = self.ui.filamentTypeComboBox.currentText
        
        if self._parameterNode:
            self._parameterNode.filamentType = filamentType #save filament type to parameter node

        self.updateFilamentProfileComboBox(printerBrand, printerModel, nozzleSize, filamentType) #update filament profiles if filament type changes
        
    def loadFilamentValues(self):
        #add original values from selected filament profile to GUI
        
        filamentFilePath = self._parameterNode.filamentProfilePath
        if not filamentFilePath:
            return
        
        values = {
            "nozzle_temperature": self.ui.nozzleTempLineEdit,
            "nozzle_temperature_initial_layer": self.ui.initialNozzleTempLineEdit,
            "hot_plate_temp": self.ui.bedTempLineEdit,
            "hot_plate_temp_initial_layer": self.ui.initialBedTempLineEdit,
            "filament_flow_ratio": self.ui.flowRatioLineEdit,
            "fan_max_speed": self.ui.fanSpeedLineEdit
        }
        
        #get desired values
        for key, lineEdit in values.items():
            value = self.logic.getFilamentProfileValues(filamentFilePath, key)
            if value:
                lineEdit.setText(str(value[0])) #if found add values to GUI
            
    def getFilamentValueChanges(self, filamentFilePath):
        #look for changed values in filament profile
        
        changes = {}
        
        values = {
            "nozzle_temperature": self.ui.nozzleTempLineEdit.text,
            "nozzle_temperature_initial_layer": self.ui.initialNozzleTempLineEdit.text,
            "hot_plate_temp": self.ui.bedTempLineEdit.text,
            "hot_plate_temp_initial_layer": self.ui.initialBedTempLineEdit.text,
            "filament_flow_ratio": self.ui.flowRatioLineEdit.text,
            "fan_max_speed": self.ui.fanSpeedLineEdit.text
        } #changable values
        
        for key, value in values.items():
            if value:
                if not value.strip(): #prevents changing value to an empty string 
                    continue
                origValue = self.logic.getFilamentProfileValues(filamentFilePath, key)
                if origValue is None or not origValue or value != str(origValue[0]): 
                    changes[key] = value #get value if changed
                    
        return changes

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
        
        segmentation = segmentationNode.GetSegmentation() #get available segmentation
        segmentIDs = vtk.vtkStringArray() #creates an empty array of strings
        segmentation.GetSegmentIDs(segmentIDs) #fill array with ID of available segmentation
        
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
        #find process profiles compatible with selected printers 
        currentDir = os.path.dirname(__file__)
        
        if printerBrand == "Bambu Lab":
            printerBrandDir = "BBL"
                
        processDir = os.path.join(currentDir, "Resources", "Profiles", printerBrandDir, "process")
        
        if not os.path.exists(processDir):
            return []
        
        compatibleProfiles = []
        
        #change printer model names based on process profile file names
        if printerModel == "P1S" or printerModel == "X1 Carbon" or printerModel == "X1E" or printerModel == "X1":
            printerModel = "X1C"
        elif printerModel == "A1 mini":
            printerModel = "A1M"
        
        printerModelRegex = rf"{re.escape(printerModel)}(?![A-Za-z0-9])" #regex to avoid confusion of A1 with A1 Mini
        
        for filename in os.listdir(processDir):
            if filename.endswith(".json"):
                if re.search(printerModelRegex, filename):
                    if nozzle == "0.4 nozzle":
                        if "nozzle" not in filename.lower(): 
                            compatibleProfiles.append(filename) #0.4 nozzle size isn't mentioned in file names
                    else:
                        if nozzle in filename.lower():
                            compatibleProfiles.append(filename)
        
        return compatibleProfiles
    
    def findMachineProfile(self, printerBrand, printerModel, nozzle):
        
        #find machine profile json file compatible with selected printed
        currentDir = os.path.dirname(__file__) #extension directory
        
        if printerBrand == "Bambu Lab": 
            printerBrandDir = "BBL"
                
        machineDir = os.path.join(currentDir, "Resources", "Profiles", printerBrandDir, "machine") 
        
        if not os.path.exists(machineDir):
            return []
        
        printerModelRegex = rf"{re.escape(printerModel)}($| \d)" #regex filter to avoid A1 Mini profile passing when A1 printer is selected
        
        #find json files for selected printer model and nozzle size
        for filename in os.listdir(machineDir):
            if filename.endswith(".json"):
                if re.search(printerModelRegex, filename):
                    if nozzle in filename.lower():
                        machineProfile = filename
                        
        machineProfilePath = os.path.join(machineDir, machineProfile)
        
        return machineProfilePath
    
    def addG92E0ToGcode(self, jsonPath) -> None:
        #adds "G92 E0 ; zero the extruder" before changing layers
        
        with open(jsonPath, "r", encoding="utf-8") as f: 
            data = json.load(f) #load data from json file
            
        data["before_layer_change_gcode"] = "G92 E0 ; zero the extruder" 
        
        with open(jsonPath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2) #rewrite json file
            
    def findFilamentProfile(self, printerBrand, printerModel, nozzleSize, filamentType):
        
        #find filament profiles compatible with selected printer and type of filament
        currentDir = os.path.dirname(__file__)
        
        if printerBrand == "Bambu Lab":
            printerBrandDir = "BBL"
            
        filamentDir = os.path.join(currentDir, "Resources", "Profiles", printerBrandDir, "filament")
        
        if not os.path.exists(filamentDir):
            return []
            
        compatibleProfile = []
        
        fullPrinterName = f"{printerBrand} {printerModel} {nozzleSize}"
        
        for filename in os.listdir(filamentDir):
            if filename.endswith(".json") and filamentType in filename:
                filePath = os.path.join(filamentDir, filename) #find filament profiles for selected material
                try:
                    with open(filePath, "r", encoding="utf-8") as f:
                        data = json.load(f) #load data from json file
                except Exception:
                    continue
                
                if fullPrinterName in data.get("compatible_printers", []):
                    compatibleProfile.append(filename) #find filamet profiles compatible with selected printer
        
        return compatibleProfile
    
    def getFilamentProfileValues(self, filamentFilePath, key, visited=None):
        #load key values from selected filament profile
        
        if visited is None: #prevent infinite recursion
            visited = set()
        
        if filamentFilePath in visited:
            return None
        visited.add(filamentFilePath)
        
        try:
            with open(filamentFilePath, "r", encoding="utf-8") as f:
                data = json.load(f) #load data from json file
        except Exception:
            return None
        
        if key in data:
            return data[key] #get wanted value 
        else:
            if "inherits" in data:
                baseFile = data["inherits"]
                basePath = os.path.join(os.path.dirname(filamentFilePath), baseFile + ".json")
                if os.path.exists(basePath):
                    return self.getFilamentProfileValues(basePath, key, visited) #look for wanted value in inherited json file
    
    def createTempFilamentProfile(self, filamentFilePath, changes):
        #create a new filament profile if user changes any values
        
        if not changes:
            return filamentFilePath
        
        try:
            with open(filamentFilePath, "r", encoding="utf-8") as f:
                baseData = json.load(f) #load data from selected filament profile
        except Exception:
            raise RuntimeError(f"Failed to load base filament profile: {filamentFilePath}")
        
        baseFilamentProfileName = baseData.get("name") #get name of the selected filament profile
        if not baseFilamentProfileName:
            raise RuntimeError("Base filament profile name missing")
        
        baseDir = os.path.dirname(filamentFilePath)
        tempFileName = f"{baseFilamentProfileName}Temp.json"
        tempFilePath = os.path.join(baseDir, tempFileName) #create new filament profile 
        
        tempData = {
            "type": "filament",
            "name": f"Temp override of {baseFilamentProfileName}",
            "inherits": baseFilamentProfileName,
            "from": "user",
            "instantiation": "true",
        } #data needed in new filament profile 
        
        for key, value in changes.items():
            tempData[key] = [str(value)] #changed values
        
        with open(tempFilePath, "w", encoding="utf-8") as f:
            json.dump(tempData, f, indent=2) #write data to new filament profile
        
        return tempFilePath
        

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

        # Test the module logic

        logic = VoxelPrintAutoLogic()
        
#TODO: Change whole test class

