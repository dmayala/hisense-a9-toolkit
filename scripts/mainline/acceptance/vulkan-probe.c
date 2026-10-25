/* Bounded headless GPU compute in the acceptance app's ordinary app domain.
 * Public numeric results only; no UUIDs, device strings or buffers are logged. */
#include <jni.h>
#include <vulkan/vulkan.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include "vulkan-shader.h"

/* device, a610, turnip, api, driver, stage, rc, submitted, fence, words, bad */
static void probe(int64_t out[11])
{
    VkInstance instance=VK_NULL_HANDLE; VkDevice device=VK_NULL_HANDLE;
    VkBuffer buffer=VK_NULL_HANDLE; VkDeviceMemory memory=VK_NULL_HANDLE;
    VkShaderModule shader=VK_NULL_HANDLE; VkDescriptorSetLayout layout=VK_NULL_HANDLE;
    VkPipelineLayout pipeline_layout=VK_NULL_HANDLE; VkPipeline pipeline=VK_NULL_HANDLE;
    VkDescriptorPool descriptors=VK_NULL_HANDLE; VkCommandPool commands=VK_NULL_HANDLE;
    VkFence fence=VK_NULL_HANDLE; void *mapped=NULL;
    VkResult rc=VK_SUCCESS;
#define CHECK(call) do { rc=(call); if(rc!=VK_SUCCESS) goto done; } while(0)
    out[5]=1;
    const char *extension=VK_KHR_GET_PHYSICAL_DEVICE_PROPERTIES_2_EXTENSION_NAME;
    VkApplicationInfo app={.sType=VK_STRUCTURE_TYPE_APPLICATION_INFO,.pApplicationName="A9 acceptance",.apiVersion=VK_API_VERSION_1_0};
    VkInstanceCreateInfo ici={.sType=VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO,.pApplicationInfo=&app,.enabledExtensionCount=1,.ppEnabledExtensionNames=&extension};
    CHECK(vkCreateInstance(&ici,NULL,&instance));
    out[5]=2;
    uint32_t count=0;CHECK(vkEnumeratePhysicalDevices(instance,&count,NULL));
    if(!count || count>16){rc=VK_ERROR_INITIALIZATION_FAILED;goto done;}
    VkPhysicalDevice physical[16];CHECK(vkEnumeratePhysicalDevices(instance,&count,physical));
    VkPhysicalDevice gpu=VK_NULL_HANDLE;
    for(uint32_t i=0;i<count;i++){
        VkPhysicalDeviceProperties properties;vkGetPhysicalDeviceProperties(physical[i],&properties);
        if(properties.vendorID!=0x5143 || !strstr(properties.deviceName,"610"))continue;
        out[0]=1;out[1]=1;out[3]=properties.apiVersion;
        uint32_t n=0;CHECK(vkEnumerateDeviceExtensionProperties(physical[i],NULL,&n,NULL));
        if(n>1024){rc=VK_ERROR_INITIALIZATION_FAILED;goto done;}
        VkExtensionProperties *extensions=calloc(n?n:1,sizeof(*extensions));
        if(!extensions){rc=VK_ERROR_OUT_OF_HOST_MEMORY;goto done;}
        rc=vkEnumerateDeviceExtensionProperties(physical[i],NULL,&n,extensions);
        int driver_properties=0;
        if(rc==VK_SUCCESS)for(uint32_t j=0;j<n;j++)if(!strcmp(extensions[j].extensionName,VK_KHR_DRIVER_PROPERTIES_EXTENSION_NAME))driver_properties=1;
        free(extensions);if(rc!=VK_SUCCESS)goto done;
        PFN_vkGetPhysicalDeviceProperties2KHR properties2=(PFN_vkGetPhysicalDeviceProperties2KHR)vkGetInstanceProcAddr(instance,"vkGetPhysicalDeviceProperties2KHR");
        if(!driver_properties || !properties2)continue;
        VkPhysicalDeviceDriverPropertiesKHR driver={.sType=VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_DRIVER_PROPERTIES_KHR};
        VkPhysicalDeviceProperties2KHR props={.sType=VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2_KHR,.pNext=&driver};
        properties2(physical[i],&props);out[4]=driver.driverID;
        if(driver.driverID!=VK_DRIVER_ID_MESA_TURNIP || !strstr(properties.deviceName,"Turnip"))continue;
        out[2]=1;gpu=physical[i];break;
    }
    if(!gpu){rc=VK_ERROR_INCOMPATIBLE_DRIVER;goto done;}
    out[5]=3;
    uint32_t family_count=0;vkGetPhysicalDeviceQueueFamilyProperties(gpu,&family_count,NULL);
    if(!family_count || family_count>64){rc=VK_ERROR_INITIALIZATION_FAILED;goto done;}
    VkQueueFamilyProperties families[64];vkGetPhysicalDeviceQueueFamilyProperties(gpu,&family_count,families);
    uint32_t family=family_count;
    for(uint32_t i=0;i<family_count;i++)if(families[i].queueCount && (families[i].queueFlags&VK_QUEUE_COMPUTE_BIT)){family=i;break;}
    if(family==family_count){rc=VK_ERROR_FEATURE_NOT_PRESENT;goto done;}
    float priority=1;
    VkDeviceQueueCreateInfo qci={.sType=VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO,.queueFamilyIndex=family,.queueCount=1,.pQueuePriorities=&priority};
    VkDeviceCreateInfo dci={.sType=VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO,.queueCreateInfoCount=1,.pQueueCreateInfos=&qci};
    CHECK(vkCreateDevice(gpu,&dci,NULL,&device));
    VkQueue queue;vkGetDeviceQueue(device,family,0,&queue);
    out[5]=4;
    VkBufferCreateInfo bci={.sType=VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO,.size=64*4,.usage=VK_BUFFER_USAGE_STORAGE_BUFFER_BIT,.sharingMode=VK_SHARING_MODE_EXCLUSIVE};
    CHECK(vkCreateBuffer(device,&bci,NULL,&buffer));
    VkMemoryRequirements requirements;vkGetBufferMemoryRequirements(device,buffer,&requirements);
    VkPhysicalDeviceMemoryProperties memories;vkGetPhysicalDeviceMemoryProperties(gpu,&memories);
    uint32_t type=memories.memoryTypeCount;
    for(uint32_t i=0;i<memories.memoryTypeCount;i++)if((requirements.memoryTypeBits&(1u<<i)) && (memories.memoryTypes[i].propertyFlags&VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT)){type=i;break;}
    if(type==memories.memoryTypeCount){rc=VK_ERROR_FEATURE_NOT_PRESENT;goto done;}
    VkMemoryAllocateInfo mai={.sType=VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO,.allocationSize=requirements.size,.memoryTypeIndex=type};
    CHECK(vkAllocateMemory(device,&mai,NULL,&memory));CHECK(vkBindBufferMemory(device,buffer,memory,0));
    CHECK(vkMapMemory(device,memory,0,VK_WHOLE_SIZE,0,&mapped));memset(mapped,0,64*4);
    VkMappedMemoryRange range={.sType=VK_STRUCTURE_TYPE_MAPPED_MEMORY_RANGE,.memory=memory,.size=VK_WHOLE_SIZE};
    CHECK(vkFlushMappedMemoryRanges(device,1,&range));
    out[5]=5;
    VkShaderModuleCreateInfo sci={.sType=VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO,.codeSize=sizeof(a9_vulkan_shader),.pCode=a9_vulkan_shader};
    CHECK(vkCreateShaderModule(device,&sci,NULL,&shader));
    VkDescriptorSetLayoutBinding binding={.binding=0,.descriptorType=VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,.descriptorCount=1,.stageFlags=VK_SHADER_STAGE_COMPUTE_BIT};
    VkDescriptorSetLayoutCreateInfo lci={.sType=VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO,.bindingCount=1,.pBindings=&binding};
    CHECK(vkCreateDescriptorSetLayout(device,&lci,NULL,&layout));
    VkPipelineLayoutCreateInfo pci={.sType=VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO,.setLayoutCount=1,.pSetLayouts=&layout};
    CHECK(vkCreatePipelineLayout(device,&pci,NULL,&pipeline_layout));
    VkComputePipelineCreateInfo compute={.sType=VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO,.stage={.sType=VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO,.stage=VK_SHADER_STAGE_COMPUTE_BIT,.module=shader,.pName="main"},.layout=pipeline_layout};
    CHECK(vkCreateComputePipelines(device,VK_NULL_HANDLE,1,&compute,NULL,&pipeline));
    VkDescriptorPoolSize size={.type=VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,.descriptorCount=1};
    VkDescriptorPoolCreateInfo dpi={.sType=VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO,.maxSets=1,.poolSizeCount=1,.pPoolSizes=&size};
    CHECK(vkCreateDescriptorPool(device,&dpi,NULL,&descriptors));
    VkDescriptorSetAllocateInfo dai={.sType=VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO,.descriptorPool=descriptors,.descriptorSetCount=1,.pSetLayouts=&layout};
    VkDescriptorSet set;CHECK(vkAllocateDescriptorSets(device,&dai,&set));
    VkDescriptorBufferInfo info={.buffer=buffer,.range=64*4};
    VkWriteDescriptorSet write={.sType=VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET,.dstSet=set,.dstBinding=0,.descriptorCount=1,.descriptorType=VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,.pBufferInfo=&info};
    vkUpdateDescriptorSets(device,1,&write,0,NULL);
    VkCommandPoolCreateInfo pool={.sType=VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO,.queueFamilyIndex=family};
    CHECK(vkCreateCommandPool(device,&pool,NULL,&commands));
    VkCommandBufferAllocateInfo cai={.sType=VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO,.commandPool=commands,.level=VK_COMMAND_BUFFER_LEVEL_PRIMARY,.commandBufferCount=1};
    VkCommandBuffer cmd;CHECK(vkAllocateCommandBuffers(device,&cai,&cmd));
    VkCommandBufferBeginInfo begin={.sType=VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO,.flags=VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT};
    CHECK(vkBeginCommandBuffer(cmd,&begin));
    vkCmdBindPipeline(cmd,VK_PIPELINE_BIND_POINT_COMPUTE,pipeline);
    vkCmdBindDescriptorSets(cmd,VK_PIPELINE_BIND_POINT_COMPUTE,pipeline_layout,0,1,&set,0,NULL);
    vkCmdDispatch(cmd,1,1,1);
    VkMemoryBarrier barrier={.sType=VK_STRUCTURE_TYPE_MEMORY_BARRIER,.srcAccessMask=VK_ACCESS_SHADER_WRITE_BIT,.dstAccessMask=VK_ACCESS_HOST_READ_BIT};
    vkCmdPipelineBarrier(cmd,VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,VK_PIPELINE_STAGE_HOST_BIT,0,1,&barrier,0,NULL,0,NULL);
    CHECK(vkEndCommandBuffer(cmd));
    VkFenceCreateInfo fci={.sType=VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};CHECK(vkCreateFence(device,&fci,NULL,&fence));
    out[5]=6;
    VkSubmitInfo submit={.sType=VK_STRUCTURE_TYPE_SUBMIT_INFO,.commandBufferCount=1,.pCommandBuffers=&cmd};
    CHECK(vkQueueSubmit(queue,1,&submit,fence));out[7]=1;
    out[5]=7;CHECK(vkWaitForFences(device,1,&fence,VK_TRUE,2000000000ull));out[8]=1;
    CHECK(vkInvalidateMappedMemoryRanges(device,1,&range));
    for(unsigned i=0;i<64;i++){out[9]++;if(((uint32_t *)mapped)[i]!=i*3+7)out[10]++;}
    out[5]=8;
done:
    out[6]=rc;
    /* Never destroy in-use resources after timeout/device loss. The sweep
     * force-stops this process, closing its DRM context without an idle wait. */
    if(out[7] && !out[8])return;
    if(mapped)vkUnmapMemory(device,memory);
    if(fence)vkDestroyFence(device,fence,NULL);
    if(commands)vkDestroyCommandPool(device,commands,NULL);
    if(descriptors)vkDestroyDescriptorPool(device,descriptors,NULL);
    if(pipeline)vkDestroyPipeline(device,pipeline,NULL);
    if(pipeline_layout)vkDestroyPipelineLayout(device,pipeline_layout,NULL);
    if(layout)vkDestroyDescriptorSetLayout(device,layout,NULL);
    if(shader)vkDestroyShaderModule(device,shader,NULL);
    if(buffer)vkDestroyBuffer(device,buffer,NULL);
    if(memory)vkFreeMemory(device,memory,NULL);
    if(device)vkDestroyDevice(device,NULL);
    if(instance)vkDestroyInstance(instance,NULL);
#undef CHECK
}
JNIEXPORT jlongArray JNICALL Java_org_a9_acceptance_Probe_vulkanProbe(JNIEnv *env,jclass clazz)
{
    (void)clazz;int64_t result[11]={0};probe(result);
    jlongArray array=(*env)->NewLongArray(env,11);
    if(array)(*env)->SetLongArrayRegion(env,array,0,11,(const jlong *)result);
    return array;
}
